"""Tests for honest_evaluator — closed-system oracle re-scoring of campaign
top builds. Spec: docs/specs/30-honest-evaluator.md."""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import optuna
import pytest

from starsector_optimizer.evaluator_pool import EvaluatorPool
from starsector_optimizer.honest_evaluator import (
    HONEST_EVAL_SCHEMA_VERSION,
    CellSummary,
    EvaluatedBuild,
    _BuildWithProvenance,
    discover_evaluation_pool,
    evaluate_builds,
    extract_top_builds,
    summarize_by_cell,
)
from starsector_optimizer.models import (
    Build,
    CombatResult,
    HonestEvaluationConfig,
    LoadoutDiagnostic,
    ShipCombatResult,
)
from starsector_optimizer.optimizer import (
    build_to_trial_params,
    define_distributions,
    trial_params_to_build,
)
from starsector_optimizer.search_space import build_search_space


# ---- Fixtures ----------------------------------------------------------------


@pytest.fixture
def hammerhead_hull(game_data):
    return game_data.hulls["hammerhead"]


@pytest.fixture
def study_db_with_n_completed(tmp_path, hammerhead_hull, game_data, manifest):
    """Factory: creates a per-study SQLite DB seeded with N completed trials
    of distinct values + valid params (round-trips through repair_build)."""
    def _make(n: int, db_name: str = "test.db", values: list[float] | None = None):
        db_path = tmp_path / db_name
        storage = f"sqlite:///{db_path}"
        from starsector_optimizer.models import REGIME_PRESETS
        space = build_search_space(
            hammerhead_hull, game_data, REGIME_PRESETS["early"], manifest,
        )
        distributions = define_distributions(space)
        study = optuna.create_study(
            study_name="hammerhead__early", storage=storage, direction="maximize",
        )
        # Generate n distinct trials by mutating one weapon slot index.
        from starsector_optimizer.calibration import generate_random_build
        import numpy as np
        rng = np.random.default_rng(42)
        for i in range(n):
            build = generate_random_build(
                hammerhead_hull, game_data, manifest,
                rng=rng, regime=REGIME_PRESETS["early"],
            )
            from starsector_optimizer.repair import repair_build
            repaired = repair_build(build, hammerhead_hull, game_data, manifest)
            params = build_to_trial_params(repaired, space)
            value = (values[i] if values else float(n - i))
            study.add_trial(optuna.create_trial(
                params=params,
                distributions=distributions,
                value=value,
            ))
        return db_path
    return _make


# ---- HonestEvaluationConfig validation ---------------------------------------


class TestHonestEvaluationConfigValidation:
    def test_rejects_zero_top_k(self):
        with pytest.raises(ValueError, match="top_k_per_seed"):
            HonestEvaluationConfig(top_k_per_seed=0)

    def test_rejects_zero_replicates(self):
        with pytest.raises(ValueError, match="replicates_per_matchup"):
            HonestEvaluationConfig(replicates_per_matchup=0)

    def test_rejects_negative_retries(self):
        with pytest.raises(ValueError, match="max_retries"):
            HonestEvaluationConfig(max_retries_per_matchup=-1)

    def test_zero_retries_is_legal(self):
        cfg = HonestEvaluationConfig(max_retries_per_matchup=0)
        assert cfg.max_retries_per_matchup == 0


# ---- extract_top_builds ------------------------------------------------------


class TestExtractTopBuilds:
    def test_returns_top_k_in_descending_value_order(
        self, study_db_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        db = study_db_with_n_completed(n=5, values=[0.1, 0.9, 0.5, 0.3, 0.7])
        tops = extract_top_builds(db, hammerhead_hull, game_data, manifest, top_k=3)
        assert len(tops) == 3
        ranks = [t[0] for t in tops]
        values = [t[1] for t in tops]
        assert ranks == [1, 2, 3]
        assert values == sorted(values, reverse=True)
        assert values[0] == pytest.approx(0.9)
        assert values[1] == pytest.approx(0.7)
        assert values[2] == pytest.approx(0.5)

    def test_raises_when_fewer_than_top_k_completed(
        self, study_db_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        db = study_db_with_n_completed(n=2)
        with pytest.raises(ValueError, match="only 2 completed"):
            extract_top_builds(db, hammerhead_hull, game_data, manifest, top_k=3)

    def test_rejects_zero_top_k(
        self, study_db_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        db = study_db_with_n_completed(n=1)
        with pytest.raises(ValueError, match="top_k must be >= 1"):
            extract_top_builds(db, hammerhead_hull, game_data, manifest, top_k=0)

    def test_raises_loud_on_stale_params(
        self, tmp_path, hammerhead_hull, game_data, manifest,
    ):
        """Stale params (not in current search space) is a data-corruption
        signal. Must raise RuntimeError, NOT silently skip — silently
        skipping would alter 'top-k' meaning and break cross-cell comparison."""
        db_path = tmp_path / "stale.db"
        storage = f"sqlite:///{db_path}"
        from starsector_optimizer.models import REGIME_PRESETS
        space = build_search_space(
            hammerhead_hull, game_data, REGIME_PRESETS["early"], manifest,
        )
        distributions = define_distributions(space)
        study = optuna.create_study(
            study_name="hammerhead__early", storage=storage, direction="maximize",
        )
        # Stale param: a weapon-slot key that doesn't exist in current
        # search space. trial_params_to_build will accept it (just dumps
        # into Build.weapons), but repair_build will reject the slot
        # because the hull has no such slot id.
        bad_distrib = optuna.distributions.CategoricalDistribution(
            choices=["empty", "phantom_weapon_id_does_not_exist"],
        )
        study.add_trial(optuna.create_trial(
            params={"weapon_NONEXISTENT_SLOT_999": "phantom_weapon_id_does_not_exist"},
            distributions={"weapon_NONEXISTENT_SLOT_999": bad_distrib},
            value=1.0,
        ))
        # A trial with a non-string weapon-id triggers repair_build's
        # type/lookup paths reliably; if even that doesn't trip repair, fall
        # through and just verify the fail-loud contract via patching.
        # Cleaner: monkeypatch repair_build to raise.
        with pytest.MonkeyPatch.context() as mp:
            def fail_repair(*a, **kw):
                raise ValueError("synthetic repair failure")
            mp.setattr(
                "starsector_optimizer.honest_evaluator.repair_build",
                fail_repair,
            )
            with pytest.raises(RuntimeError, match="failed repair_build"):
                extract_top_builds(
                    db_path, hammerhead_hull, game_data, manifest, top_k=1,
                )

    def test_ignores_pruned_and_running_trials(
        self, tmp_path, hammerhead_hull, game_data, manifest,
    ):
        """Only COMPLETE trials with non-None value count toward top_k."""
        db_path = tmp_path / "mixed_states.db"
        storage = f"sqlite:///{db_path}"
        from starsector_optimizer.models import REGIME_PRESETS
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.repair import repair_build
        import numpy as np
        space = build_search_space(
            hammerhead_hull, game_data, REGIME_PRESETS["early"], manifest,
        )
        distributions = define_distributions(space)
        study = optuna.create_study(
            study_name="hammerhead__early", storage=storage, direction="maximize",
        )
        rng = np.random.default_rng(7)
        # Add one COMPLETE trial.
        b = repair_build(
            generate_random_build(hammerhead_hull, game_data, manifest, rng=rng, regime=REGIME_PRESETS["early"]),
            hammerhead_hull, game_data, manifest,
        )
        study.add_trial(optuna.create_trial(
            params=build_to_trial_params(b, space),
            distributions=distributions,
            value=0.5,
        ))
        # Add a PRUNED trial — should NOT count toward top_k.
        b2 = repair_build(
            generate_random_build(hammerhead_hull, game_data, manifest, rng=rng, regime=REGIME_PRESETS["early"]),
            hammerhead_hull, game_data, manifest,
        )
        study.add_trial(optuna.create_trial(
            params=build_to_trial_params(b2, space),
            distributions=distributions,
            state=optuna.trial.TrialState.PRUNED,
        ))
        # Asking for top_k=2 must raise (only 1 COMPLETE).
        with pytest.raises(ValueError, match="only 1 completed"):
            extract_top_builds(db_path, hammerhead_hull, game_data, manifest, top_k=2)


# ---- discover_evaluation_pool -----------------------------------------------


class TestDiscoverEvaluationPool:
    def test_returns_same_size_opponents_no_active_cap(
        self, game_dir, game_data, hammerhead_hull,
    ):
        pool = discover_evaluation_pool(game_dir, game_data, hammerhead_hull)
        # Hammerhead is DESTROYER; pool returns DESTROYER-keyed bucket.
        # Cap MUST NOT be applied — full population, no `active_opponents`.
        from starsector_optimizer.opponent_pool import discover_opponent_pool, get_opponents
        full = get_opponents(
            discover_opponent_pool(game_dir, game_data),
            hammerhead_hull.hull_size,
        )
        assert pool == full
        assert len(pool) > 10  # sanity: vanilla DESTROYER bucket has ~50+


# ---- evaluate_builds ---------------------------------------------------------


def _make_combat_result(matchup_id: str, winner: str = "PLAYER",
                       duration: float = 60.0) -> CombatResult:
    from starsector_optimizer.models import DamageBreakdown
    p = ShipCombatResult(
        fleet_member_id="p", variant_id="v_p", hull_id="hammerhead",
        destroyed=False, hull_fraction=1.0, armor_fraction=1.0,
        cr_remaining=0.7, peak_time_remaining=60.0,
        disabled_weapons=0, flameouts=0,
        damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
        overload_count=0,
    )
    e = ShipCombatResult(
        fleet_member_id="e", variant_id="v_e", hull_id="wolf",
        destroyed=True, hull_fraction=0.0, armor_fraction=0.0,
        cr_remaining=0.0, peak_time_remaining=0.0,
        disabled_weapons=0, flameouts=0,
        damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
        overload_count=0,
    )
    return CombatResult(
        matchup_id=matchup_id, winner=winner, duration_seconds=duration,
        player_ships=(p,), enemy_ships=(e,),
        player_ships_destroyed=0, enemy_ships_destroyed=1,
        player_ships_retreated=0, enemy_ships_retreated=0,
        player_loadout_diagnostics=(),
    )


class _MockPool(EvaluatorPool):
    """Synchronous mock; records every dispatched matchup_id."""
    def __init__(self, num_workers: int = 4):
        self._num_workers = num_workers
        self.dispatched: list[str] = []

    def setup(self) -> None: ...
    def teardown(self) -> None: ...

    @property
    def num_workers(self) -> int:
        return self._num_workers

    def run_matchup(self, matchup):
        self.dispatched.append(matchup.matchup_id)
        return _make_combat_result(matchup.matchup_id, winner="PLAYER")


class _FlakyPool(EvaluatorPool):
    """Fails the first N total dispatches, succeeds after."""
    def __init__(self, fail_first: int):
        self._fail = fail_first
        self._count = 0
        self.attempts = 0

    def setup(self) -> None: ...
    def teardown(self) -> None: ...

    @property
    def num_workers(self) -> int:
        return 2

    def run_matchup(self, matchup):
        self.attempts += 1
        if self._count < self._fail:
            self._count += 1
            raise RuntimeError(f"synthetic failure #{self._count}")
        return _make_combat_result(matchup.matchup_id, winner="PLAYER")


class _AlwaysFailPool(EvaluatorPool):
    def setup(self) -> None: ...
    def teardown(self) -> None: ...

    @property
    def num_workers(self) -> int:
        return 2

    def run_matchup(self, matchup):
        raise RuntimeError("always fails")


def _bp(build: Build, campaign: str = "test", rank: int = 1) -> _BuildWithProvenance:
    return _BuildWithProvenance(
        build=build, source_campaign=campaign, source_study_idx=0,
        source_seed_idx=0, source_rank=rank, source_value=0.5,
    )


class TestEvaluateBuilds:
    def test_uniqueness_of_dispatched_matchup_ids_across_replicates(
        self, hammerhead_hull, game_data, manifest,
    ):
        """Critical: matchup_id must include rep-index so CloudWorkerPool's
        _seen dedupe doesn't silently drop replicate dispatches."""
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.repair import repair_build
        from starsector_optimizer.models import REGIME_PRESETS
        import numpy as np
        rng = np.random.default_rng(1)
        b = repair_build(
            generate_random_build(hammerhead_hull, game_data, manifest, rng=rng, regime=REGIME_PRESETS["early"]),
            hammerhead_hull, game_data, manifest,
        )
        pool = _MockPool()
        eval_pool = ("opp_a", "opp_b")
        cfg = HonestEvaluationConfig(replicates_per_matchup=4, max_retries_per_matchup=0)
        evaluate_builds([_bp(b)], eval_pool, pool, cfg, hammerhead_hull)
        # 1 build × 2 opponents × 4 replicates = 8 unique IDs
        assert len(pool.dispatched) == 8
        assert len(set(pool.dispatched)) == 8, (
            f"replicate IDs collided: {[m for m in pool.dispatched]}"
        )
        # Verify the pattern includes _rep{N}
        for mid in pool.dispatched:
            assert "_rep" in mid, f"missing _rep suffix in {mid}"

    def test_aggregates_mean_fitness_across_pool_and_replicates(
        self, hammerhead_hull, game_data, manifest,
    ):
        """oracle_score = mean of combat_fitness across all matchups."""
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.repair import repair_build
        from starsector_optimizer.models import REGIME_PRESETS
        from starsector_optimizer.combat_fitness import combat_fitness
        import numpy as np
        rng = np.random.default_rng(2)
        b = repair_build(
            generate_random_build(hammerhead_hull, game_data, manifest, rng=rng, regime=REGIME_PRESETS["early"]),
            hammerhead_hull, game_data, manifest,
        )
        pool = _MockPool()
        eval_pool = ("opp_x", "opp_y", "opp_z")
        cfg = HonestEvaluationConfig(replicates_per_matchup=2, max_retries_per_matchup=0)
        result = evaluate_builds([_bp(b)], eval_pool, pool, cfg, hammerhead_hull)
        assert len(result) == 1
        eb = result[0]
        # All matchups return PLAYER win — fitness is a constant per-matchup.
        per_match = combat_fitness(_make_combat_result("x", "PLAYER"), config=cfg.fitness_config)
        assert eb.oracle_score == pytest.approx(per_match)
        assert eb.oracle_se == pytest.approx(0.0, abs=1e-9)
        assert eb.n_matchups_succeeded == 6  # 3 opps × 2 reps

    def test_retries_failed_matchups_then_succeeds(
        self, hammerhead_hull, game_data, manifest,
    ):
        """Transient failures retry up to max_retries; if eventually
        succeed, oracle counts the success result."""
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.repair import repair_build
        from starsector_optimizer.models import REGIME_PRESETS
        import numpy as np
        rng = np.random.default_rng(3)
        b = repair_build(
            generate_random_build(hammerhead_hull, game_data, manifest, rng=rng, regime=REGIME_PRESETS["early"]),
            hammerhead_hull, game_data, manifest,
        )
        pool = _FlakyPool(fail_first=2)
        eval_pool = ("opp_a",)
        cfg = HonestEvaluationConfig(replicates_per_matchup=3, max_retries_per_matchup=2)
        result = evaluate_builds([_bp(b)], eval_pool, pool, cfg, hammerhead_hull)
        # 3 dispatches + 2 retries = 5 total attempts
        assert pool.attempts == 5
        assert result[0].n_matchups_succeeded == 3

    def test_raises_when_matchup_fails_all_retries(
        self, hammerhead_hull, game_data, manifest,
    ):
        """Persistent failures must raise — silent exclusion would break
        the balanced-design guarantee underlying the oracle."""
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.repair import repair_build
        from starsector_optimizer.models import REGIME_PRESETS
        import numpy as np
        rng = np.random.default_rng(4)
        b = repair_build(
            generate_random_build(hammerhead_hull, game_data, manifest, rng=rng, regime=REGIME_PRESETS["early"]),
            hammerhead_hull, game_data, manifest,
        )
        pool = _AlwaysFailPool()
        eval_pool = ("opp_a",)
        cfg = HonestEvaluationConfig(replicates_per_matchup=1, max_retries_per_matchup=2)
        with pytest.raises(RuntimeError, match="failed after 2 retries"):
            evaluate_builds([_bp(b)], eval_pool, pool, cfg, hammerhead_hull)

    def test_empty_eval_pool_raises(
        self, hammerhead_hull, game_data, manifest,
    ):
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.repair import repair_build
        from starsector_optimizer.models import REGIME_PRESETS
        import numpy as np
        rng = np.random.default_rng(5)
        b = repair_build(
            generate_random_build(hammerhead_hull, game_data, manifest, rng=rng, regime=REGIME_PRESETS["early"]),
            hammerhead_hull, game_data, manifest,
        )
        cfg = HonestEvaluationConfig()
        with pytest.raises(ValueError, match="eval_pool is empty"):
            evaluate_builds([_bp(b)], (), _MockPool(), cfg, hammerhead_hull)

    def test_empty_builds_raises(self, hammerhead_hull):
        cfg = HonestEvaluationConfig()
        with pytest.raises(ValueError, match="no builds"):
            evaluate_builds([], ("opp_a",), _MockPool(), cfg, hammerhead_hull)


# ---- summarize_by_cell -------------------------------------------------------


class TestSummarizeByCell:
    def _eb(self, campaign: str, score: float, se: float = 0.01,
            rank: int = 1) -> EvaluatedBuild:
        return EvaluatedBuild(
            build=Build(hull_id="hammerhead", weapon_assignments={},
                        hullmods=frozenset(),
                        flux_vents=0, flux_capacitors=0),
            source_campaign=campaign, source_study_idx=0, source_seed_idx=0,
            source_rank=rank, source_value=0.5,
            oracle_score=score, oracle_se=se, n_matchups_succeeded=10,
        )

    def test_orders_cells_by_descending_mean_top_k(self):
        builds = [
            self._eb("c0a", 0.10, rank=1),
            self._eb("c0a", 0.20, rank=2),
            self._eb("c2", 0.50, rank=1),
            self._eb("c2", 0.40, rank=2),
            self._eb("c1", 0.30, rank=1),
        ]
        summaries = summarize_by_cell(builds)
        assert [s.cell_name for s in summaries] == ["c2", "c1", "c0a"]
        assert summaries[0].mean_top_k_oracle == pytest.approx(0.45)
        assert summaries[2].mean_top_k_oracle == pytest.approx(0.15)

    def test_best_build_oracle_is_max_within_cell(self):
        builds = [
            self._eb("c2", 0.50, se=0.02, rank=1),
            self._eb("c2", 0.40, se=0.03, rank=2),
            self._eb("c2", 0.45, se=0.01, rank=3),
        ]
        s = summarize_by_cell(builds)[0]
        assert s.cell_name == "c2"
        assert s.best_build_oracle == pytest.approx(0.50)
        assert s.best_build_se == pytest.approx(0.02)
        assert s.n_builds_evaluated == 3

    def test_empty_input_returns_empty_tuple(self):
        assert summarize_by_cell([]) == ()


# ---- Schema version ----------------------------------------------------------


class TestSchemaVersion:
    def test_schema_version_is_pinned(self):
        """Bumping HONEST_EVAL_SCHEMA_VERSION is a deliberate breaking-change
        gate — pin the value here so accidental bumps fail the test."""
        assert HONEST_EVAL_SCHEMA_VERSION == 1


# ---- main() CLI wiring -------------------------------------------------------


def _write_smoke_campaign_yaml(tmp_path):
    """Minimal campaign YAML for honest-eval-main tests."""
    import yaml
    cfg = {
        "name": "ut-honest-eval-source",
        "budget_usd": 5.0,
        "provider": "aws",
        "regions": ["us-east-1"],
        "instance_types": ["c7a.2xlarge"],
        "spot_allocation_strategy": "price-capacity-optimized",
        "capacity_rebalancing": True,
        "max_concurrent_workers": 8,
        "min_workers_to_start": 1,
        "partial_fleet_policy": "abort",
        "ami_ids_by_region": {"us-east-1": "ami-abc"},
        "ssh_key_name": "starsector-probe",
        "tailscale_authkey_secret": "tskey-auth-SMOKE-44e7f9b3",
        "studies": [{
            "hull": "hammerhead", "regime": "early", "seeds": [0],
            "budget_per_study": 50, "workers_per_study": 6, "sampler": "tpe",
        }],
        "max_lifetime_hours": 0.5,
    }
    path = tmp_path / "ut-honest-eval-source.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


class TestMainCLIWiring:
    """End-to-end wiring for `honest_evaluator.main`.

    Mocks AWS / Redis / Flask to avoid network I/O. Asserts that:
      - dry-run exits 0 without calling provider/pool
      - real run threads through prepare_cloud_pool with the
        honest-eval-namespaced fleet/project_tag
      - `--workers` overrides default (max workers_per_study)
      - missing source-campaign YAML raises ValueError
    """

    def _seed_campaign_dbs(
        self, db_root, campaign_name, study_db_factory, n_seeds=1, n_trials=3,
    ):
        cdir = db_root / campaign_name
        cdir.mkdir(parents=True, exist_ok=True)
        for seed_idx in range(n_seeds):
            db = study_db_factory(
                n=n_trials,
                db_name=f"hammerhead__early__tpe__seed{seed_idx}.db",
            )
            # Move the file into the campaign dir.
            target = cdir / db.name
            target.write_bytes(db.read_bytes())
        return cdir

    def _patch_manifest_load(self, monkeypatch, manifest):
        """main() calls GameManifest.load() with the default path; under
        tmp_path chdir that relative path is missing. Patch to return the
        session manifest fixture."""
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.GameManifest.load",
            classmethod(lambda cls, path=None: manifest),
        )

    def _patch_preflight(self, monkeypatch):
        """`_preflight_for_honest_eval` calls boto3 STS + AMI describe —
        mock to no-op so tests don't need real AWS. The real preflight
        is exercised in `test_preflight_rejects_bad_authkey` and
        `test_preflight_rejects_stale_ami_tag`."""
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator._preflight_for_honest_eval",
            lambda campaign, authkey, manifest: None,
        )

    def test_dry_run_skips_provisioning(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """--dry-run exits 0 without touching AWSProvider / CloudWorkerPool."""
        from starsector_optimizer import honest_evaluator
        # Seed a per-study DB under data/study_dbs/<name>/
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        # honest_evaluator.main hardcodes Path("data/study_dbs") — chdir +
        # patch GameManifest.load (relative path won't resolve under tmp).
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        # Move YAML into examples/ at the new CWD.
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        # Sentinel: prepare_cloud_pool must NOT be called.
        sentinel = MagicMock(side_effect=AssertionError("provisioned in dry-run"))
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool", sentinel,
        )

        rc = honest_evaluator.main([
            "--campaign-name", "ut-honest-eval-source",
            "--hull", "hammerhead",
            "--game-dir", str(game_dir),
            "--top-k", "1",
            "--dry-run",
        ])
        assert rc == 0
        sentinel.assert_not_called()

    def test_full_run_uses_honest_eval_namespace(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """The honest-eval fleet is tagged honest-eval-* — distinct from any
        source-campaign fleet's project_tag/study_id/fleet_name. This is
        the contract that prevents `terminate_all_tagged` from reaching the
        wrong fleet."""
        from starsector_optimizer import honest_evaluator
        # Seed dbs.
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        # Capture the kwargs that flow into prepare_cloud_pool, then return
        # a fake context manager that yields a mock pool.
        captured: dict = {}
        from contextlib import contextmanager

        @contextmanager
        def fake_prepare(**kwargs):
            captured.update(kwargs)
            yield MagicMock(num_workers=2)

        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool",
            fake_prepare,
        )
        # Stub evaluate_builds so we don't need a working pool.run_matchup.
        fake_evaluated = ()
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.evaluate_builds",
            lambda *a, **kw: fake_evaluated,
        )
        # write_outputs writes to disk under tmp_path — fine.

        rc = honest_evaluator.main([
            "--campaign-name", "ut-honest-eval-source",
            "--hull", "hammerhead",
            "--game-dir", str(game_dir),
            "--top-k", "1",
            "--out-root", str(tmp_path / "out"),
        ])
        assert rc == 0
        # Honest-eval namespace: all four naming fields must be the same
        # honest-eval-{name}-{stamp} string and must NOT match the source
        # campaign's project_tag.
        assert captured["study_id"].startswith("honest-eval-ut-honest-eval-source-")
        assert captured["project_tag"] == captured["study_id"]
        assert captured["fleet_name"] == captured["study_id"]
        assert captured["project_tag"] != smoke_env["STARSECTOR_PROJECT_TAG"]

    def test_workers_override_changes_target(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        captured: dict = {}
        from contextlib import contextmanager

        @contextmanager
        def fake_prepare(**kwargs):
            captured.update(kwargs)
            yield MagicMock(num_workers=2)

        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool",
            fake_prepare,
        )
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.evaluate_builds",
            lambda *a, **kw: (),
        )
        # Default would be max(workers_per_study) == 6; override to 3.
        rc = honest_evaluator.main([
            "--campaign-name", "ut-honest-eval-source",
            "--hull", "hammerhead", "--game-dir", str(game_dir),
            "--top-k", "1", "--workers", "3",
            "--out-root", str(tmp_path / "out"),
        ])
        assert rc == 0
        assert captured["target_workers"] == 3
        # total_matchup_slots = workers * matchup_slots_per_worker (default 2)
        assert captured["total_matchup_slots"] == 6

    def test_default_flask_port_is_inside_acl_range(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """V1 regression — earlier default `base_flask_port - 1` (= 8999)
        was OUTSIDE the tailnet ACL `tcp:9000-9099`, causing every worker
        POST to silently time out while the fleet billed. The default must
        be in [base, base + flask_ports_per_study)."""
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        captured: dict = {}
        from contextlib import contextmanager

        @contextmanager
        def fake_prepare(**kwargs):
            captured.update(kwargs)
            yield MagicMock(num_workers=2)

        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool",
            fake_prepare,
        )
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.evaluate_builds",
            lambda *a, **kw: (),
        )

        rc = honest_evaluator.main([
            "--campaign-name", "ut-honest-eval-source",
            "--hull", "hammerhead", "--game-dir", str(game_dir),
            "--top-k", "1", "--out-root", str(tmp_path / "out"),
        ])
        assert rc == 0
        # Default = base_flask_port + flask_ports_per_study - 1 = 9000 + 100 - 1
        assert captured["flask_port"] == 9099
        # Must be inside ACL range tcp:9000-9099
        assert 9000 <= captured["flask_port"] <= 9099

    def test_out_of_range_flask_port_raises_pre_provision(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """Operator-supplied --flask-port outside [base, base + ports_per_study)
        must fail BEFORE provisioning any AWS resources — workers can't
        POST through the ACL otherwise."""
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        sentinel = MagicMock(side_effect=AssertionError("provisioned despite bad port"))
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool", sentinel,
        )

        with pytest.raises(ValueError, match="outside the tailnet-ACL range"):
            honest_evaluator.main([
                "--campaign-name", "ut-honest-eval-source",
                "--hull", "hammerhead", "--game-dir", str(game_dir),
                "--top-k", "1", "--flask-port", "8999",
                "--out-root", str(tmp_path / "out"),
            ])
        sentinel.assert_not_called()

    def test_unrecognized_db_filename_raises_loud(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """IG3 — unrecognized DB filename in the campaign dir is a
        data-integrity signal (drift from the per-study layout). Must
        raise, not warn-and-skip."""
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        cdir = self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        # Drop a misnamed file alongside the valid one.
        (cdir / "stray.db").write_bytes((cdir / "hammerhead__early__tpe__seed0.db").read_bytes())
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)

        with pytest.raises(RuntimeError, match="unrecognized DB filename"):
            honest_evaluator.main([
                "--campaign-name", "ut-honest-eval-source",
                "--hull", "hammerhead", "--game-dir", str(game_dir),
                "--top-k", "1",
            ])

    def test_preflight_rejects_stale_ami_tag(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """Manifest+AMI tag drift = silent oracle corruption (workers run
        pre-G probe code against v2 manifest). Must raise BEFORE
        provisioning. Tests the real `_preflight_for_honest_eval`'s AMI
        check via injected fake provider."""
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        # NOT patching preflight — testing the real one. STS still needs
        # to be mocked since CI has no AWS creds.
        import boto3
        monkeypatch.setattr(boto3, "client", lambda *a, **kw: MagicMock(
            get_caller_identity=lambda: {"UserId": "ut"},
        ))
        # Provider returns a stale ModCommitSha so the gate trips.
        class FakeProvider:
            def __init__(self, *, regions):
                self.regions = regions
            def describe_ami_tag(self, *, ami_id, region, tag_key):
                if tag_key == "GameVersion":
                    return manifest.constants.game_version
                if tag_key == "ModCommitSha":
                    return "deadbeef0000000"  # mismatched
                raise KeyError(tag_key)
        monkeypatch.setattr(
            honest_evaluator, "AWSProvider", FakeProvider,
        )
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        sentinel = MagicMock(side_effect=AssertionError("provisioned despite stale AMI"))
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool", sentinel,
        )

        with pytest.raises(ValueError, match="ModCommitSha"):
            honest_evaluator.main([
                "--campaign-name", "ut-honest-eval-source",
                "--hull", "hammerhead", "--game-dir", str(game_dir),
                "--top-k", "1",
            ])
        sentinel.assert_not_called()

    def test_preflight_passes_when_ami_tags_match(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """Sanity: the AMI gate doesn't false-positive when tags match."""
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        import boto3
        monkeypatch.setattr(boto3, "client", lambda *a, **kw: MagicMock(
            get_caller_identity=lambda: {"UserId": "ut"},
        ))
        class FakeProvider:
            def __init__(self, *, regions):
                self.regions = regions
            def describe_ami_tag(self, *, ami_id, region, tag_key):
                if tag_key == "GameVersion":
                    return manifest.constants.game_version
                if tag_key == "ModCommitSha":
                    return manifest.constants.mod_commit_sha
                raise KeyError(tag_key)
        monkeypatch.setattr(
            honest_evaluator, "AWSProvider", FakeProvider,
        )
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        captured: dict = {}
        from contextlib import contextmanager
        @contextmanager
        def fake_prepare(**kwargs):
            captured.update(kwargs)
            yield MagicMock(num_workers=2)
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool",
            fake_prepare,
        )
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.evaluate_builds",
            lambda *a, **kw: (),
        )
        rc = honest_evaluator.main([
            "--campaign-name", "ut-honest-eval-source",
            "--hull", "hammerhead", "--game-dir", str(game_dir),
            "--top-k", "1", "--out-root", str(tmp_path / "out"),
        ])
        assert rc == 0
        # Provisioning was reached, meaning preflight passed.
        assert "study_id" in captured

    def test_preflight_rejects_bad_authkey(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """IG1 — malformed authkey should fail BEFORE provisioning. Tests
        the real `_preflight_for_honest_eval` (not the patched stub)."""
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        # NOT patching preflight — testing the real one.
        # Override the smoke_env authkey to a malformed value.
        monkeypatch.setenv("STARSECTOR_TAILSCALE_AUTHKEY", "tskey-WRONG-PREFIX")
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        sentinel = MagicMock(side_effect=AssertionError("provisioned despite bad authkey"))
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool", sentinel,
        )

        # Helper raises PreflightFailure (a ValueError subclass) with
        # message wording owned by `campaign.check_authkey_syntax`.
        with pytest.raises(ValueError, match=r"must start with .tskey-auth-"):
            honest_evaluator.main([
                "--campaign-name", "ut-honest-eval-source",
                "--hull", "hammerhead", "--game-dir", str(game_dir),
                "--top-k", "1",
            ])
        sentinel.assert_not_called()

    def test_eval_tag_length_guard_rejects_overlong_name(
        self, monkeypatch, tmp_path,
    ):
        """M2 — eval_tag = honest-eval-{name}-{16-char-stamp}; AWS Launch
        Template names cap at 128 chars after AWSProvider doubles it.
        Verify the guard fires."""
        from starsector_optimizer.honest_evaluator import _validate_eval_tag_length
        # 60-char tag is the boundary; 61 must raise.
        ok = "x" * 60
        _validate_eval_tag_length(ok)
        bad = "x" * 61
        with pytest.raises(ValueError, match="overflow AWS Launch Template"):
            _validate_eval_tag_length(bad)

    def test_empty_eval_pool_raises_pre_provision(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        """An empty eval pool would otherwise be discovered AFTER fleet
        boot (when evaluate_builds runs inside the `with` block); the
        pre-provision check saves ~30s of fleet-boot cost."""
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)

        # Force discover_evaluation_pool to return ().
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.discover_evaluation_pool",
            lambda *a, **kw: (),
        )
        sentinel = MagicMock(side_effect=AssertionError("provisioned with empty pool"))
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool", sentinel,
        )
        with pytest.raises(ValueError, match="no compatible opponents"):
            honest_evaluator.main([
                "--campaign-name", "ut-honest-eval-source",
                "--hull", "hammerhead", "--game-dir", str(game_dir),
                "--top-k", "1",
            ])
        sentinel.assert_not_called()

    def test_missing_campaign_yaml_raises_clear_error(
        self, monkeypatch, tmp_path, smoke_env, study_db_with_n_completed,
        game_dir, manifest,
    ):
        from starsector_optimizer import honest_evaluator
        db_root = tmp_path / "data" / "study_dbs"
        self._seed_campaign_dbs(
            db_root, "ut-honest-eval-source", study_db_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        # Don't create examples/ut-honest-eval-source.yaml — main() must raise.

        with pytest.raises(ValueError, match="campaign config not found"):
            honest_evaluator.main([
                "--campaign-name", "ut-honest-eval-source",
                "--hull", "hammerhead", "--game-dir", str(game_dir),
                "--top-k", "1",
            ])
