"""Tests for honest_evaluator — closed-system oracle re-scoring of campaign
top builds. Spec: docs/specs/30-honest-evaluator.md."""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock

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


@pytest.fixture(autouse=True)
def _clean_worker_source_checkout(monkeypatch):
    """Unit tests run against an intentionally dirty worktree while editing."""
    monkeypatch.setattr(
        "starsector_optimizer.campaign._worker_source_dirty_status",
        lambda: "",
    )
    monkeypatch.setattr(
        "starsector_optimizer.campaign.worker_source_sha256",
        lambda: "worker-sha",
    )


# ---- Fixtures ----------------------------------------------------------------


@pytest.fixture
def hammerhead_hull(game_data):
    return game_data.hulls["hammerhead"]


@pytest.fixture
def study_jsonl_with_n_completed(tmp_path, hammerhead_hull, game_data, manifest):
    """Factory: writes a per-study `evaluation_log.jsonl` with N completed
    trials, mirroring the optimizer's row schema.

    `intermediate_means` controls the per-trial mean of per-match
    `hp_differential` — which is what `extract_top_builds` ranks by
    post-2026-05-10 (TWFE+EB on the [build × opponent] matrix). If
    omitted, defaults to a descending sequence so the highest-ranked
    trial is index 0.
    """
    def _make(
        n: int,
        log_subdir: str = "test_campaign/hammerhead__early__tpe__seed0",
        intermediate_means: list[float] | None = None,
        n_opps_per_trial: int = 4,
    ):
        log_path = tmp_path / "data" / "logs" / log_subdir / "evaluation_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.models import REGIME_PRESETS
        from starsector_optimizer.repair import repair_build
        import numpy as np
        rng = np.random.default_rng(42)
        with log_path.open("w") as f:
            for i in range(n):
                build = generate_random_build(
                    hammerhead_hull, game_data, manifest,
                    rng=rng, regime=REGIME_PRESETS["early"],
                )
                repaired = repair_build(build, hammerhead_hull, game_data, manifest)
                mean = (
                    float(intermediate_means[i])
                    if intermediate_means is not None else float(n - i)
                )
                # Synthesize n_opps_per_trial opponents, each with the same
                # hp_differential = mean. Ranking by per-build mean ⇒ exactly
                # `mean`. Winner inferred from sign of mean for BT compatibility.
                if mean > 0.1:
                    winner = "PLAYER"
                elif mean < -0.1:
                    winner = "ENEMY"
                else:
                    winner = "TIMEOUT"
                results = [
                    {
                        "opponent": f"opp_{j}",
                        "winner": winner,
                        "duration_seconds": 30.0,
                        "hp_differential": mean,
                    }
                    for j in range(n_opps_per_trial)
                ]
                row = {
                    "trial_number": i,
                    "build": {
                        "hull_id": repaired.hull_id,
                        "weapon_assignments": dict(repaired.weapon_assignments),
                        "hullmods": list(repaired.hullmods),
                        "flux_vents": repaired.flux_vents,
                        "flux_capacitors": repaired.flux_capacitors,
                    },
                    "opponent_results": results,
                    "pruned": False,
                    "cache_hit": False,
                    "invalid_spec": False,
                    "raw_fitness": mean,
                    "fitness": mean,
                }
                f.write(json.dumps(row) + "\n")
        return log_path
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

    def test_rejects_nonpositive_cloud_lifetime_headroom(self):
        with pytest.raises(ValueError, match="cloud_lifetime_headroom"):
            HonestEvaluationConfig(cloud_lifetime_headroom=0)

    def test_rejects_nonpositive_cloud_min_lifetime(self):
        with pytest.raises(ValueError, match="cloud_min_lifetime_hours"):
            HonestEvaluationConfig(cloud_min_lifetime_hours=0)

    def test_zero_retries_is_legal(self):
        cfg = HonestEvaluationConfig(max_retries_per_matchup=0)
        assert cfg.max_retries_per_matchup == 0


# ---- extract_top_builds ------------------------------------------------------


class TestExtractTopBuilds:
    def test_returns_top_k_in_descending_score_order(
        self, study_jsonl_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        log = study_jsonl_with_n_completed(
            n=5, intermediate_means=[0.1, 0.9, 0.5, 0.3, 0.7],
        )
        tops = extract_top_builds(
            log, hammerhead_hull, game_data, manifest, top_k=3,
            method="raw_mean",
        )
        assert len(tops) == 3
        ranks = [t[0] for t in tops]
        scores = [t[1] for t in tops]
        assert ranks == [1, 2, 3]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == pytest.approx(0.9)
        assert scores[1] == pytest.approx(0.7)
        assert scores[2] == pytest.approx(0.5)

    def test_twfe_eb_default_orders_by_residual(
        self, study_jsonl_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        """With identical opponents per build (no confounding), TWFE+EB
        recovers the same ordering as raw mean — the regression to the
        mean is a magnitude rescale, not a re-rank. Verifies the default
        `method` works end-to-end on a JSONL fixture."""
        log = study_jsonl_with_n_completed(
            n=5, intermediate_means=[0.1, 0.9, 0.5, 0.3, 0.7],
        )
        tops = extract_top_builds(
            log, hammerhead_hull, game_data, manifest, top_k=3,
        )  # method='twfe_eb' default
        assert len(tops) == 3
        # Same trial-index ordering as raw mean (build i=1 with mean=0.9 first).
        # Magnitudes shrink toward zero relative to raw means.
        scores = [t[1] for t in tops]
        assert scores == sorted(scores, reverse=True)

    def test_raises_when_log_path_missing(
        self, tmp_path, hammerhead_hull, game_data, manifest,
    ):
        with pytest.raises(FileNotFoundError, match="no evaluation_log.jsonl"):
            extract_top_builds(
                tmp_path / "nonexistent.jsonl",
                hammerhead_hull, game_data, manifest, top_k=1,
            )

    def test_raises_when_fewer_than_top_k_completed(
        self, study_jsonl_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        log = study_jsonl_with_n_completed(n=2)
        with pytest.raises(ValueError, match="only 2 completed"):
            extract_top_builds(
                log, hammerhead_hull, game_data, manifest, top_k=3,
            )

    def test_rejects_zero_top_k(
        self, study_jsonl_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        log = study_jsonl_with_n_completed(n=1)
        with pytest.raises(ValueError, match="top_k must be >= 1"):
            extract_top_builds(
                log, hammerhead_hull, game_data, manifest, top_k=0,
            )

    def test_rejects_unknown_method(
        self, study_jsonl_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        log = study_jsonl_with_n_completed(n=3)
        with pytest.raises(ValueError, match="unknown ranking method"):
            extract_top_builds(
                log, hammerhead_hull, game_data, manifest, top_k=1,
                method="bogus",
            )

    def test_raises_loud_on_repair_failure(
        self, study_jsonl_with_n_completed, hammerhead_hull, game_data, manifest,
    ):
        """Stale build spec (e.g. a hullmod that no longer exists in the
        current manifest) is a data-corruption signal. Must raise
        RuntimeError, not silently skip."""
        log = study_jsonl_with_n_completed(n=3)
        with pytest.MonkeyPatch.context() as mp:
            def fail_repair(*a, **kw):
                raise ValueError("synthetic repair failure")
            mp.setattr(
                "starsector_optimizer.honest_evaluator.repair_build",
                fail_repair,
            )
            with pytest.raises(RuntimeError, match="failed repair_build"):
                extract_top_builds(
                    log, hammerhead_hull, game_data, manifest, top_k=1,
                )

    def test_skips_pruned_and_invalid_rows(
        self, tmp_path, hammerhead_hull, game_data, manifest,
    ):
        """Pruned, cache-hit, and invalid-spec rows must not count toward
        top_k. Only completed rows (with non-empty opponent_results) are
        legal candidates."""
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.models import REGIME_PRESETS
        from starsector_optimizer.repair import repair_build
        import numpy as np
        log_path = tmp_path / "evaluation_log.jsonl"
        rng = np.random.default_rng(11)

        def _row(i, **flags):
            build = generate_random_build(
                hammerhead_hull, game_data, manifest,
                rng=rng, regime=REGIME_PRESETS["early"],
            )
            repaired = repair_build(
                build, hammerhead_hull, game_data, manifest,
            )
            base = {
                "trial_number": i,
                "build": {
                    "hull_id": repaired.hull_id,
                    "weapon_assignments": dict(repaired.weapon_assignments),
                    "hullmods": list(repaired.hullmods),
                    "flux_vents": repaired.flux_vents,
                    "flux_capacitors": repaired.flux_capacitors,
                },
                "opponent_results": [{
                    "opponent": "opp_0", "winner": "PLAYER",
                    "duration_seconds": 30.0, "hp_differential": 0.5,
                }],
                "pruned": False, "cache_hit": False, "invalid_spec": False,
            }
            base.update(flags)
            return json.dumps(base) + "\n"

        log_path.write_text(
            _row(0)
            + _row(1, pruned=True)
            + _row(2, cache_hit=True)
            + _row(3, invalid_spec=True),
        )
        # Only 1 completed row — top_k=2 must raise.
        with pytest.raises(ValueError, match="only 1 completed"):
            extract_top_builds(
                log_path, hammerhead_hull, game_data, manifest, top_k=2,
            )


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


class TestRandomBaseline:
    """Auditor C (2026-05-10): without a random-feasible baseline cell,
    even successful Wave 1 honest-eval can't answer 'does any optimization
    machinery beat random sampling?'. The synthesized baseline gives the
    cross-cell ranking an existence check."""

    def test_synthesizes_n_distinct_feasible_builds(
        self, hammerhead_hull, game_data, manifest,
    ):
        from starsector_optimizer.honest_evaluator import (
            RANDOM_BASELINE_SOURCE_CAMPAIGN, synthesize_random_baseline_builds,
        )
        from starsector_optimizer.repair import is_feasible
        builds = synthesize_random_baseline_builds(
            hammerhead_hull, game_data, manifest, n=5, seed=42,
        )
        assert len(builds) == 5
        for bp in builds:
            assert bp.source_campaign == RANDOM_BASELINE_SOURCE_CAMPAIGN
            assert is_feasible(bp.build, hammerhead_hull, game_data, manifest)
        # Ranks should be 1..5 (deterministic order from rng).
        assert [bp.source_rank for bp in builds] == [1, 2, 3, 4, 5]

    def test_deterministic_in_seed(
        self, hammerhead_hull, game_data, manifest,
    ):
        """Resume contract: re-running with the same seed produces the
        same baseline builds, so the ledger's (build_id, opp, rep) keys
        match across a resume."""
        from starsector_optimizer.honest_evaluator import (
            synthesize_random_baseline_builds,
        )
        a = synthesize_random_baseline_builds(
            hammerhead_hull, game_data, manifest, n=3, seed=7,
        )
        b = synthesize_random_baseline_builds(
            hammerhead_hull, game_data, manifest, n=3, seed=7,
        )
        for bp_a, bp_b in zip(a, b):
            assert bp_a.build == bp_b.build

    def test_different_seeds_produce_different_builds(
        self, hammerhead_hull, game_data, manifest,
    ):
        from starsector_optimizer.honest_evaluator import (
            synthesize_random_baseline_builds,
        )
        a = synthesize_random_baseline_builds(
            hammerhead_hull, game_data, manifest, n=3, seed=0,
        )
        b = synthesize_random_baseline_builds(
            hammerhead_hull, game_data, manifest, n=3, seed=1,
        )
        # Should not all be the same.
        assert any(bp_a.build != bp_b.build for bp_a, bp_b in zip(a, b))


class TestLedgerResume:
    """The ledger is the data-loss fix: every successful matchup result
    is appended to JSONL with fsync, and a future run with the same
    eval_tag replays the ledger to skip already-completed matchups
    instead of redoing the work.
    """

    def _make_build(self, hull, game_data, manifest, seed=0):
        from starsector_optimizer.calibration import generate_random_build
        from starsector_optimizer.repair import repair_build
        from starsector_optimizer.models import REGIME_PRESETS
        import numpy as np
        rng = np.random.default_rng(seed)
        return repair_build(
            generate_random_build(hull, game_data, manifest, rng=rng,
                                  regime=REGIME_PRESETS["early"]),
            hull, game_data, manifest,
        )

    def test_writes_ledger_line_per_successful_matchup(
        self, hammerhead_hull, game_data, manifest, tmp_path,
    ):
        from starsector_optimizer.honest_evaluator import (
            LEDGER_SCHEMA_VERSION, evaluate_builds,
        )
        b = self._make_build(hammerhead_hull, game_data, manifest, seed=10)
        pool = _MockPool()
        eval_pool = ("opp_a", "opp_b")
        cfg = HonestEvaluationConfig(
            replicates_per_matchup=2, max_retries_per_matchup=0,
        )
        ledger_path = tmp_path / "results.jsonl"
        evaluate_builds(
            [_bp(b)], eval_pool, pool, cfg, hammerhead_hull,
            ledger_path=ledger_path,
        )
        # 1 build × 2 opps × 2 reps = 4 dispatched ⇒ 4 ledger lines
        assert ledger_path.exists()
        lines = [ln for ln in ledger_path.read_text().splitlines() if ln]
        assert len(lines) == 4
        for ln in lines:
            data = json.loads(ln)
            assert data["schema_version"] == LEDGER_SCHEMA_VERSION
            assert "build_id" in data
            assert "opponent_variant_id" in data
            assert "replicate_idx" in data
            assert isinstance(data["fitness"], float)
            assert "completed_at" in data

    def test_resume_skips_completed_and_dispatches_only_remaining(
        self, hammerhead_hull, game_data, manifest, tmp_path,
    ):
        """Ledger replay: first run writes 4 entries, then a fresh pool
        with the same ledger should dispatch ZERO matchups (all already
        completed) and still return aggregated results."""
        from starsector_optimizer.honest_evaluator import evaluate_builds
        b = self._make_build(hammerhead_hull, game_data, manifest, seed=11)
        eval_pool = ("opp_a", "opp_b")
        cfg = HonestEvaluationConfig(
            replicates_per_matchup=2, max_retries_per_matchup=0,
        )
        ledger_path = tmp_path / "results.jsonl"

        # First run — writes ledger.
        pool1 = _MockPool()
        evaluate_builds(
            [_bp(b)], eval_pool, pool1, cfg, hammerhead_hull,
            ledger_path=ledger_path,
        )
        assert len(pool1.dispatched) == 4

        # Second run with same ledger — must skip everything.
        pool2 = _MockPool()
        result = evaluate_builds(
            [_bp(b)], eval_pool, pool2, cfg, hammerhead_hull,
            ledger_path=ledger_path,
        )
        assert pool2.dispatched == [], (
            "ledger replay must skip dispatch for already-completed matchups"
        )
        # The aggregation still produces a result.
        assert result[0].n_matchups_succeeded == 4

    def test_resume_dispatches_only_missing_matchups(
        self, hammerhead_hull, game_data, manifest, tmp_path,
    ):
        """Partial ledger: pre-seed 2 entries, then run; expect only the
        OTHER 2 matchups (4 total - 2 seeded) to be dispatched."""
        from starsector_optimizer.honest_evaluator import (
            LEDGER_SCHEMA_VERSION, _LedgerWriter, LedgerEntry, evaluate_builds,
        )
        b = self._make_build(hammerhead_hull, game_data, manifest, seed=12)
        bp = _bp(b)
        eval_pool = ("opp_a", "opp_b")
        cfg = HonestEvaluationConfig(
            replicates_per_matchup=2, max_retries_per_matchup=0,
        )
        ledger_path = tmp_path / "results.jsonl"
        # Pre-seed: opp_a × rep0 + opp_a × rep1.
        bid = (
            f"honest__{bp.source_campaign}__s{bp.source_study_idx}"
            f"__seed{bp.source_seed_idx}__rank{bp.source_rank}"
        )
        writer = _LedgerWriter(ledger_path)
        for rep in range(2):
            writer.append(LedgerEntry(
                schema_version=LEDGER_SCHEMA_VERSION,
                matchup_id=f"{bid}_vs_opp_a_rep{rep}",
                build_id=bid,
                opponent_variant_id="opp_a",
                replicate_idx=rep,
                fitness=0.5,
                completed_at="2026-05-10T00:00:00+00:00",
            ))

        pool = _MockPool()
        result = evaluate_builds(
            [bp], eval_pool, pool, cfg, hammerhead_hull,
            ledger_path=ledger_path,
        )
        # Only opp_b × {rep0, rep1} should dispatch.
        assert len(pool.dispatched) == 2
        for mid in pool.dispatched:
            assert "opp_b" in mid
        # Final aggregation: 2 from ledger + 2 fresh = 4.
        assert result[0].n_matchups_succeeded == 4

    def test_unknown_build_id_in_ledger_raises(
        self, hammerhead_hull, game_data, manifest, tmp_path,
    ):
        """A ledger entry whose build_id no longer maps to any current
        build means --top-k or campaign DBs changed. Refuse to mix old
        and new scores silently."""
        from starsector_optimizer.honest_evaluator import (
            LEDGER_SCHEMA_VERSION, _LedgerWriter, LedgerEntry, evaluate_builds,
        )
        b = self._make_build(hammerhead_hull, game_data, manifest, seed=13)
        ledger_path = tmp_path / "results.jsonl"
        writer = _LedgerWriter(ledger_path)
        writer.append(LedgerEntry(
            schema_version=LEDGER_SCHEMA_VERSION,
            matchup_id="ghost__vs_opp_a_rep0",
            build_id="ghost__c_z__s9__seed9__rank9",
            opponent_variant_id="opp_a",
            replicate_idx=0,
            fitness=0.1,
            completed_at="2026-05-10T00:00:00+00:00",
        ))
        cfg = HonestEvaluationConfig(
            replicates_per_matchup=1, max_retries_per_matchup=0,
        )
        with pytest.raises(RuntimeError, match="unknown build_id"):
            evaluate_builds(
                [_bp(b)], ("opp_a",), _MockPool(), cfg, hammerhead_hull,
                ledger_path=ledger_path,
            )

    def test_corrupt_ledger_line_raises(self, tmp_path):
        from starsector_optimizer.honest_evaluator import read_ledger
        ledger_path = tmp_path / "results.jsonl"
        # Valid first line, garbage second.
        ledger_path.write_text(
            '{"schema_version": 1, "matchup_id": "m1", "build_id": "ok", '
            '"opponent_variant_id": "o", "replicate_idx": 0, '
            '"fitness": 0.1, "completed_at": "2026-05-10T00:00:00+00:00"}\n'
            'not-json garbage\n'
        )
        with pytest.raises(RuntimeError, match="corrupt ledger line"):
            read_ledger(ledger_path)

    def test_old_schema_version_skipped_with_warning(self, tmp_path, caplog):
        from starsector_optimizer.honest_evaluator import read_ledger
        ledger_path = tmp_path / "results.jsonl"
        ledger_path.write_text(
            '{"schema_version": 999, "build_id": "x", '
            '"opponent_variant_id": "o", "replicate_idx": 0, '
            '"fitness": 0.1}\n'
        )
        with caplog.at_level("WARNING"):
            completed = read_ledger(ledger_path)
        assert completed == {}
        assert any("schema_version=999" in r.message for r in caplog.records)


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


def _write_smoke_campaign_yaml(tmp_path, **overrides):
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
    cfg.update(overrides)
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

    def _seed_campaign_logs(
        self, log_root, campaign_name, study_jsonl_factory,
        n_seeds=1, n_trials=3,
    ):
        """Seed `data/logs/<campaign>/<study-stem>/evaluation_log.jsonl`
        for each seed. Mirrors the path layout main() globs.

        Returns the campaign log dir (e.g. `data/logs/<campaign>/`).
        """
        cdir = log_root / campaign_name
        cdir.mkdir(parents=True, exist_ok=True)
        for seed_idx in range(n_seeds):
            stem = f"hammerhead__early__tpe__seed{seed_idx}"
            log = study_jsonl_factory(
                n=n_trials, log_subdir=f"{campaign_name}/{stem}",
            )
            # study_jsonl_factory writes under tmp_path/data/logs/...; copy
            # to the test's chosen log_root if different.
            target = cdir / stem / "evaluation_log.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            if log != target:
                target.write_bytes(log.read_bytes())
        return cdir

    def _patch_manifest_load(self, monkeypatch, manifest):
        """main() calls GameManifest.load() with the default path; under
        tmp_path chdir that relative path is missing. Patch to return the
        session manifest fixture."""
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.GameManifest.load",
            classmethod(lambda cls, path=None: manifest),
        )
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator._flush_stale_campaign_keys",
            lambda *a, **kw: 0,
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
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """--dry-run exits 0 without touching AWSProvider / CloudWorkerPool."""
        from starsector_optimizer import honest_evaluator
        # Seed a per-study evaluation_log.jsonl under data/logs/<name>/<study-stem>/
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
        )
        # honest_evaluator.main hardcodes Path("data/logs") — chdir +
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

    def test_dry_run_does_not_flush_redis_keys(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        flush = MagicMock(side_effect=AssertionError("flushed during dry-run"))
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator._flush_stale_campaign_keys",
            flush,
        )

        rc = honest_evaluator.main([
            "--campaign-name", "ut-honest-eval-source",
            "--hull", "hammerhead",
            "--game-dir", str(game_dir),
            "--top-k", "1",
            "--dry-run",
        ])
        assert rc == 0
        flush.assert_not_called()

    def test_full_run_uses_honest_eval_namespace(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """The honest-eval fleet is tagged starsector-honest-eval-* —
        distinct from any source-campaign fleet's project_tag/study_id/
        fleet_name. The `starsector-` prefix matches CampaignManager and
        teardown.sh; the `honest-eval-` segment prevents
        `terminate_all_tagged` from reaching the wrong fleet."""
        from starsector_optimizer import honest_evaluator
        # Seed dbs.
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
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
        # starsector-honest-eval-{name}-{stamp} string and must NOT match
        # the source campaign's project_tag. The `starsector-` prefix is
        # required for teardown.sh to find the fleet.
        assert captured["study_id"].startswith(
            "starsector-honest-eval-ut-honest-eval-source-"
        )
        assert captured["project_tag"] == captured["study_id"]
        assert captured["fleet_name"] == captured["study_id"]
        assert captured["project_tag"] != smoke_env["STARSECTOR_PROJECT_TAG"]
        assert captured["sweep_project_on_exit"] is True
        assert captured["campaign"].max_lifetime_hours > 0.5
        assert (
            captured["campaign"].visibility_timeout_seconds
            > captured["campaign"].result_timeout_seconds
        )

    def test_full_run_flushes_redis_before_prepare_cloud_pool(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        events: list[tuple[str, tuple]] = []
        captured: dict = {}
        from contextlib import contextmanager

        def fake_flush(*args):
            events.append(("flush", args))
            return 0

        @contextmanager
        def fake_prepare(**kwargs):
            events.append(("prepare", (kwargs["study_id"],)))
            captured.update(kwargs)
            yield MagicMock(num_workers=2)

        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator._flush_stale_campaign_keys",
            fake_flush,
        )
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
            "--hull", "hammerhead",
            "--game-dir", str(game_dir),
            "--top-k", "1",
            "--out-root", str(tmp_path / "out"),
        ])
        assert rc == 0
        assert [event for event, _ in events] == ["flush", "prepare"]
        flush_args = events[0][1]
        assert flush_args == (
            captured["study_id"],
            captured["campaign"].redis_port,
            captured["campaign"].redis_preflight_timeout_seconds,
        )

    def test_honest_eval_cloud_timing_adjustment_raises_short_training_limits(
        self, tmp_path,
    ):
        """Honest eval must not inherit short source-campaign cloud timing.

        Wave 1's source YAML had max_lifetime_hours=2 and default
        visibility_timeout_seconds=120. A large oracle sweep outlives both.
        """
        from starsector_optimizer.campaign import load_campaign_config
        from starsector_optimizer.honest_evaluator import (
            _adjust_campaign_for_honest_eval,
        )

        cfg_path = _write_smoke_campaign_yaml(
            tmp_path,
            max_lifetime_hours=0.5,
            visibility_timeout_seconds=120.0,
            result_timeout_seconds=900.0,
            janitor_interval_seconds=60.0,
        )
        campaign = load_campaign_config(cfg_path)
        adjusted = _adjust_campaign_for_honest_eval(
            campaign,
            total_matchups=87_480,
            total_matchup_slots=128,
            config=HonestEvaluationConfig(max_retries_per_matchup=3),
        )
        assert adjusted.max_lifetime_hours > campaign.max_lifetime_hours
        assert adjusted.visibility_timeout_seconds == 3660.0

    def test_signal_handlers_route_sigterm_and_sighup_to_keyboard_interrupt(
        self, monkeypatch,
    ):
        """`kill <pid>` must unwind Python context managers instead of using
        the process-default SIGTERM action."""
        import signal
        from starsector_optimizer import honest_evaluator

        installed = {}
        monkeypatch.setattr(
            signal, "signal",
            lambda sig, handler: installed.setdefault(sig, handler),
        )

        honest_evaluator._install_signal_handlers()
        assert signal.SIGTERM in installed
        assert signal.SIGHUP in installed
        with pytest.raises(KeyboardInterrupt, match="received signal"):
            installed[signal.SIGTERM](signal.SIGTERM, None)
        # A repeated signal during cleanup must not interrupt the teardown
        # path that the first signal triggered.
        installed[signal.SIGTERM](signal.SIGTERM, None)

    def test_keyboard_interrupt_returns_130_after_cloud_context_unwinds(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """If SIGTERM/SIGHUP maps to KeyboardInterrupt inside the cloud
        context, `prepare_cloud_pool.__exit__` must run before main returns
        130."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)
        (tmp_path / "examples").mkdir()
        yaml_src = _write_smoke_campaign_yaml(tmp_path)
        (tmp_path / "examples" / "ut-honest-eval-source.yaml").write_bytes(
            yaml_src.read_bytes()
        )

        events: list[str] = []

        class FakeContext:
            def __enter__(self):
                events.append("enter")
                return MagicMock(num_workers=2)

            def __exit__(self, exc_type, exc, tb):
                events.append("exit")

        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.prepare_cloud_pool",
            lambda **kwargs: FakeContext(),
        )
        monkeypatch.setattr(
            "starsector_optimizer.honest_evaluator.evaluate_builds",
            lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()),
        )

        rc = honest_evaluator.main([
            "--campaign-name", "ut-honest-eval-source",
            "--hull", "hammerhead",
            "--game-dir", str(game_dir),
            "--top-k", "1",
            "--out-root", str(tmp_path / "out"),
        ])
        assert rc == 130
        assert events == ["enter", "exit"]

    def test_workers_override_changes_target(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
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
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """V1 regression — earlier default `base_flask_port - 1` (= 8999)
        was OUTSIDE the tailnet ACL `tcp:9000-9099`, causing every worker
        POST to silently time out while the fleet billed. The default must
        be in [base, base + flask_ports_per_study)."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
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
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """Operator-supplied --flask-port outside [base, base + ports_per_study)
        must fail BEFORE provisioning any AWS resources — workers can't
        POST through the ACL otherwise."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
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

    def test_unrecognized_log_dir_raises_loud(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """IG3 — unrecognized study-dir name in `data/logs/<campaign>/`
        is a data-integrity signal (drift from the per-study layout).
        Must raise, not warn-and-skip."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        cdir = self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
        )
        # Drop a misnamed study dir (no `__seedN` suffix) alongside the
        # valid one. The glob `*/evaluation_log.jsonl` will match it; the
        # seed-parsing must raise loud rather than silently skip.
        stray = cdir / "stray__no_seed_suffix"
        stray.mkdir()
        (stray / "evaluation_log.jsonl").write_bytes(
            (cdir / "hammerhead__early__tpe__seed0" /
             "evaluation_log.jsonl").read_bytes()
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        self._patch_preflight(monkeypatch)

        with pytest.raises(RuntimeError, match="unrecognized log dir"):
            honest_evaluator.main([
                "--campaign-name", "ut-honest-eval-source",
                "--hull", "hammerhead", "--game-dir", str(game_dir),
                "--top-k", "1",
            ])

    def test_preflight_rejects_stale_ami_tag(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """Manifest+AMI tag drift = silent oracle corruption (workers run
        pre-G probe code against v2 manifest). Must raise BEFORE
        provisioning. Tests the real `_preflight_for_honest_eval`'s AMI
        check via injected fake provider."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        monkeypatch.setattr(
            "starsector_optimizer.campaign.worker_source_sha256",
            lambda: "worker-sha",
        )
        monkeypatch.setattr(
            "starsector_optimizer.campaign.manifest_sha256",
            lambda: "manifest-sha",
        )
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
                if tag_key == "ManifestSha256":
                    return "manifest-sha"
                if tag_key == "ModCommitSha":
                    return "deadbeef0000000"  # mismatched
                if tag_key == "WorkerSourceSha":
                    return "worker-sha"
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
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """Sanity: the AMI gate doesn't false-positive when tags match."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
        )
        monkeypatch.chdir(tmp_path)
        self._patch_manifest_load(monkeypatch, manifest)
        monkeypatch.setattr(
            "starsector_optimizer.campaign.worker_source_sha256",
            lambda: "worker-sha",
        )
        monkeypatch.setattr(
            "starsector_optimizer.campaign.manifest_sha256",
            lambda: "manifest-sha",
        )
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
                if tag_key == "ManifestSha256":
                    return "manifest-sha"
                if tag_key == "ModCommitSha":
                    return manifest.constants.mod_commit_sha
                if tag_key == "WorkerSourceSha":
                    return "worker-sha"
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
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """IG1 — malformed authkey should fail BEFORE provisioning. Tests
        the real `_preflight_for_honest_eval` (not the patched stub)."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
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
        """M2 — eval_tag = starsector-honest-eval-{name}-{16-char-stamp};
        AWS Launch Template names cap at 128 chars after AWSProvider
        doubles `{project_tag}__{fleet_name}` (= 2 × eval_tag + 2).
        Boundary is `MAX_EVAL_TAG_LEN`."""
        from starsector_optimizer.honest_evaluator import (
            MAX_EVAL_TAG_LEN, _validate_eval_tag_length,
        )
        ok = "x" * MAX_EVAL_TAG_LEN
        _validate_eval_tag_length(ok)
        bad = "x" * (MAX_EVAL_TAG_LEN + 1)
        with pytest.raises(ValueError, match="overflow AWS Launch Template"):
            _validate_eval_tag_length(bad)

    def test_empty_eval_pool_raises_pre_provision(
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        """An empty eval pool would otherwise be discovered AFTER fleet
        boot (when evaluate_builds runs inside the `with` block); the
        pre-provision check saves ~30s of fleet-boot cost."""
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
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
        self, monkeypatch, tmp_path, smoke_env, study_jsonl_with_n_completed,
        game_dir, manifest,
    ):
        from starsector_optimizer import honest_evaluator
        log_root = tmp_path / "data" / "logs"
        self._seed_campaign_logs(
            log_root, "ut-honest-eval-source", study_jsonl_with_n_completed,
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
