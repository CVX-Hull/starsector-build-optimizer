"""Tests for Phase 7 prior-run build recovery and materialization."""

import json
import sqlite3
from pathlib import Path

import pytest

from starsector_optimizer.models import Build
from starsector_optimizer.repair import is_feasible
from starsector_optimizer.phase7_matchup_data import (
    BURNED_SPLIT_SEEDS,
    DUPLICATE_SPLIT_STATUS,
    INSUFFICIENCY_STATUSES,
    SPLIT_SEED_EXCLUSIONS,
    STALE_EXCLUSION_STATUS,
    BuildSourceKind,
    ComponentVocabularySplit,
    HonestEvalMatchupRow,
    RecoveredBuild,
    SplitIds,
    TrainingMatchupRow,
    build_from_log_row,
    build_key,
    component_fingerprint_json,
    component_vocabulary,
    forward_time_split,
    grouped_kfold,
    held_out_component_vocabulary_split,
    held_out_build_split,
    held_out_opponent_family_split,
    held_out_opponent_hull_split,
    held_out_opponent_split,
    held_out_replicate_split,
    held_out_seed_cell_split,
    iter_honest_eval_matchups,
    iter_training_matchups,
    materialize_sqlite,
    recover_honest_eval_candidate_builds,
    recover_honest_eval_output_builds,
    recover_logged_builds,
    recover_study_db_builds,
    honest_build_id_to_key,
    reject_excluded_split_seed,
    split_partition_sha256,
)


def _sample_log_row() -> dict:
    return {
        "hull_id": "hammerhead",
        "trial_number": 8,
        "build": {
            "hull_id": "hammerhead",
            "weapon_assignments": {"WS 001": "heavyac", "WS 002": None},
            "hullmods": ["fluxcoil", "armoredweapons"],
            "flux_vents": 4,
            "flux_capacitors": 2,
        },
        "opponent_results": [
            {"opponent": "enforcer_Balanced", "hp_differential": 0.25, "winner": "player"},
            {"opponent": "sunder_Support", "hp_differential": -0.5, "winner": "opponent"},
        ],
        "pruned": False,
        "raw_fitness": 0.1,
        "fitness": 0.2,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _write_fixture_study_db(path: Path, *, unsupported: bool = False) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table trials (
            trial_id integer primary key,
            number integer,
            study_id integer,
            state varchar(8),
            datetime_start datetime,
            datetime_complete datetime
        );
        create table trial_values (
            trial_value_id integer primary key,
            trial_id integer not null,
            objective integer not null,
            value real,
            value_type varchar(7) not null
        );
        create table trial_params (
            param_id integer primary key,
            trial_id integer,
            param_name varchar(512),
            param_value real,
            distribution_json text
        );
        """
    )
    con.execute(
        "insert into trials (trial_id, number, study_id, state) values (1, 0, 1, 'COMPLETE')"
    )
    con.execute(
        "insert into trial_values (trial_id, objective, value, value_type) "
        "values (1, 0, 0.25, 'FINITE')"
    )
    params = [
        (
            "weapon_WS 001",
            1.0,
            {"name": "CategoricalDistribution", "attributes": {"choices": ["empty", "heavyac"]}},
        ),
        (
            "weapon_WS 002",
            1.0,
            {
                "name": "CategoricalDistribution",
                "attributes": {"choices": ["empty", "heavymortar"]},
            },
        ),
        (
            "hullmod_fluxcoil",
            0.0,
            {"name": "CategoricalDistribution", "attributes": {"choices": [True, False]}},
        ),
        (
            "flux_vents",
            4.0,
            {
                "name": "IntDistribution",
                "attributes": {"low": 0, "high": 30, "step": 1, "log": False},
            },
        ),
        (
            "flux_capacitors",
            2.0,
            {
                "name": "IntDistribution",
                "attributes": {"low": 0, "high": 30, "step": 1, "log": False},
            },
        ),
    ]
    if unsupported:
        params.append(("bad_param", 0.0, {"name": "UnsupportedDistribution", "attributes": {}}))
    for idx, (name, value, dist) in enumerate(params, start=1):
        con.execute(
            "insert into trial_params "
            "(param_id, trial_id, param_name, param_value, distribution_json) "
            "values (?, 1, ?, ?, ?)",
            (idx, name, value, json.dumps(dist)),
        )
    con.commit()
    con.close()


class TestCanonicalBuilds:
    def test_build_key_is_order_insensitive(self):
        a = Build(
            "hammerhead",
            {"WS 002": None, "WS 001": "heavyac"},
            frozenset({"fluxcoil", "armoredweapons"}),
            4,
            2,
        )
        b = Build(
            "hammerhead",
            {"WS 001": "heavyac", "WS 002": None},
            frozenset({"armoredweapons", "fluxcoil"}),
            4,
            2,
        )
        assert build_key(a) == build_key(b)

    def test_build_from_log_row(self):
        build = build_from_log_row(_sample_log_row())
        assert build.hull_id == "hammerhead"
        assert build.weapon_assignments["WS 001"] == "heavyac"
        assert build.weapon_assignments["WS 002"] is None
        assert build.hullmods == frozenset({"fluxcoil", "armoredweapons"})

    def test_component_fingerprint_uses_full_canonical_build(self):
        a = Build(
            "hammerhead", {"WS 001": "heavyac", "WS 002": None}, frozenset({"fluxcoil"}), 4, 2
        )
        b = Build(
            "hammerhead", {"WS 001": None, "WS 002": "heavyac"}, frozenset({"fluxcoil"}), 4, 2
        )
        c = Build("enforcer", {"WS 001": "heavyac", "WS 002": None}, frozenset({"fluxcoil"}), 4, 2)
        d = Build(
            "hammerhead", {"WS 001": "heavyac", "WS 002": None}, frozenset({"fluxcoil"}), 5, 2
        )

        assert component_fingerprint_json(a) != component_fingerprint_json(b)
        assert component_fingerprint_json(a) != component_fingerprint_json(c)
        assert component_fingerprint_json(a) != component_fingerprint_json(d)


class TestLogRecovery:
    def test_recover_logged_builds_tags_exact_source(self, tmp_path):
        log_dir = tmp_path / "data" / "logs" / "phase7-ablation" / "hammerhead__early__tpe__seed0"
        log_dir.mkdir(parents=True)
        path = log_dir / "evaluation_log.jsonl"
        _write_jsonl(path, [_sample_log_row()])

        recovered = recover_logged_builds([path])

        assert len(recovered) == 1
        assert recovered[0].source_kind == BuildSourceKind.EXACT_LOGGED_BUILD
        assert recovered[0].campaign == "phase7-ablation"
        assert recovered[0].trial_number == 8
        assert recovered[0].score == 0.1

    def test_recover_logged_builds_skips_invalid_spec_rows(self, tmp_path):
        path = tmp_path / "evaluation_log.jsonl"
        invalid = _sample_log_row() | {"invalid_spec": True}
        _write_jsonl(path, [invalid])

        assert recover_logged_builds([path]) == ()

    def test_iter_training_matchups_emits_one_row_per_opponent(self, tmp_path):
        path = tmp_path / "evaluation_log.jsonl"
        _write_jsonl(path, [_sample_log_row()])

        rows = list(iter_training_matchups([path]))

        assert len(rows) == 2
        assert rows[0].opponent_variant_id == "enforcer_Balanced"
        assert rows[0].target == 0.25
        assert rows[0].row_kind == "finalized"


class TestDbRecovery:
    def test_db_reconstruction_decodes_params_repairs_and_is_feasible(
        self, tmp_path, game_data, manifest
    ):
        db_path = tmp_path / "study.db"
        _write_fixture_study_db(db_path)
        hull = game_data.hulls["hammerhead"]

        recovered = recover_study_db_builds(
            db_path,
            hull,
            game_data,
            manifest,
            campaign="wave1-c0a",
            study="hammerhead__early__tpe__seed0",
            seed=0,
        )

        assert len(recovered) == 1
        item = recovered[0]
        assert item.source_kind == BuildSourceKind.DB_RECONSTRUCTED_BUILD
        assert item.build.weapon_assignments["WS 001"] == "heavyac"
        assert item.build.flux_vents >= 0
        feasible, violations = is_feasible(item.build, hull, game_data, manifest)
        assert feasible, violations

    def test_unsupported_distribution_raises(self, tmp_path, game_data, manifest):
        db_path = tmp_path / "study.db"
        _write_fixture_study_db(db_path, unsupported=True)

        with pytest.raises(ValueError, match="unsupported Optuna distribution"):
            recover_study_db_builds(db_path, game_data.hulls["hammerhead"], game_data, manifest)


class TestHonestEvalRecovery:
    def test_recover_honest_eval_candidate_builds(self, tmp_path, game_data, manifest):
        log_dir = tmp_path / "wave1-c0a" / "hammerhead__early__tpe__seed0"
        log_dir.mkdir(parents=True)
        path = log_dir / "evaluation_log.jsonl"
        _write_jsonl(path, [_sample_log_row()])

        recovered = recover_honest_eval_candidate_builds(
            [path],
            game_data.hulls["hammerhead"],
            game_data,
            manifest,
            top_k=1,
            method="raw_mean",
        )

        assert len(recovered) == 1
        assert recovered[0].source_kind == BuildSourceKind.HONEST_EVAL_CANDIDATE_BUILD
        assert recovered[0].rank == 1

    def test_iter_honest_eval_matchups_joins_build_key(self, tmp_path):
        path = tmp_path / "results.jsonl"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "matchup_id": "m",
                    "build_id": "honest__wave1-c0a__s0__seed0__rank1",
                    "opponent_variant_id": "enforcer_Balanced",
                    "replicate_idx": 2,
                    "fitness": 0.75,
                    "completed_at": "2026-05-11T00:00:00+00:00",
                }
            )
            + "\n"
        )

        rows = list(iter_honest_eval_matchups(path, {"honest__wave1-c0a__s0__seed0__rank1": "abc"}))

        assert rows == [
            HonestEvalMatchupRow(
                source_path=str(path),
                build_id="honest__wave1-c0a__s0__seed0__rank1",
                build_key="abc",
                opponent_variant_id="enforcer_Balanced",
                replicate_idx=2,
                target=0.75,
            )
        ]

    def test_recover_honest_eval_output_builds_maps_random_baseline(self, tmp_path):
        path = tmp_path / "honest_eval.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "campaign": "random-baseline",
                    "evaluated_builds": [
                        {
                            "build": _sample_log_row()["build"],
                            "source_campaign": "random-baseline",
                            "source_study_idx": 0,
                            "source_seed_idx": 0,
                            "source_rank": 7,
                            "source_value": None,
                            "oracle_score": -0.25,
                            "oracle_se": 0.01,
                            "n_matchups_succeeded": 1620,
                        }
                    ],
                }
            )
        )

        recovered = recover_honest_eval_output_builds([path])
        mapping = honest_build_id_to_key(recovered)

        assert len(recovered) == 1
        assert recovered[0].source_kind == BuildSourceKind.HONEST_EVAL_OUTPUT_BUILD
        assert recovered[0].campaign == "random-baseline"
        assert recovered[0].study == "s0"
        assert mapping == {"honest__random-baseline__s0__seed0__rank7": recovered[0].build_key}


class TestSqliteMaterialization:
    def test_materialize_sqlite_round_trip(self, tmp_path):
        build = build_from_log_row(_sample_log_row())
        recovered = RecoveredBuild(
            build_key=build_key(build),
            build=build,
            source_kind=BuildSourceKind.EXACT_LOGGED_BUILD,
            campaign="wave1-c0a",
            study="seed0",
            seed=0,
            rank=None,
            trial_number=8,
            score=0.1,
            source_path="evaluation_log.jsonl",
        )
        matchup = TrainingMatchupRow(
            source_path="evaluation_log.jsonl",
            campaign="wave1-c0a",
            seed=0,
            trial_number=8,
            build_key=recovered.build_key,
            opponent_variant_id="enforcer_Balanced",
            opponent_index=0,
            target=0.25,
            row_kind="finalized",
        )
        honest = HonestEvalMatchupRow(
            source_path="results.jsonl",
            build_id="honest__wave1-c0a__s0__seed0__rank1",
            build_key=recovered.build_key,
            opponent_variant_id="enforcer_Balanced",
            replicate_idx=0,
            target=0.5,
        )
        db_path = tmp_path / "phase7.sqlite"

        materialize_sqlite(
            db_path,
            recovered_builds=[recovered],
            training_matchups=[matchup],
            honest_eval_matchups=[honest],
        )

        con = sqlite3.connect(db_path)
        assert con.execute("select count(*) from recovered_builds").fetchone()[0] == 1
        assert con.execute("select count(*) from training_matchups").fetchone()[0] == 1
        assert con.execute("select count(*) from honest_eval_matchups").fetchone()[0] == 1
        assert (
            con.execute("select source_kind from recovered_builds").fetchone()[0]
            == "exact_logged_build"
        )


def _split_rows() -> list[TrainingMatchupRow]:
    return [
        TrainingMatchupRow("p", "c", 0, i, f"b{i % 4}", f"opp{i % 3}", i, float(i), "finalized")
        for i in range(12)
    ]


def _as_training_row(row: TrainingMatchupRow | HonestEvalMatchupRow) -> TrainingMatchupRow:
    """Narrow a split row to TrainingMatchupRow (these tests only feed training rows)."""
    assert isinstance(row, TrainingMatchupRow)
    return row


class TestSplitBuilders:
    def test_held_out_build_split_keeps_builds_disjoint(self):
        split = held_out_build_split(_split_rows(), holdout_fraction=0.25, seed=1)
        train_builds = {row.build_key for row in split.train}
        test_builds = {row.build_key for row in split.test}
        assert train_builds.isdisjoint(test_builds)
        assert split.test

    def test_held_out_opponent_split_keeps_opponents_disjoint(self):
        split = held_out_opponent_split(_split_rows(), holdout_fraction=0.34, seed=1)
        train_opps = {row.opponent_variant_id for row in split.train}
        test_opps = {row.opponent_variant_id for row in split.test}
        assert train_opps.isdisjoint(test_opps)
        assert split.test

    def test_held_out_opponent_hull_split_keeps_hulls_disjoint(self):
        mapping = {"opp0": "enforcer", "opp1": "sunder", "opp2": "wolf"}

        split = held_out_opponent_hull_split(_split_rows(), mapping, holdout_fraction=0.34, seed=1)

        train_hulls = {mapping[row.opponent_variant_id] for row in split.train}
        test_hulls = {mapping[row.opponent_variant_id] for row in split.test}
        assert train_hulls.isdisjoint(test_hulls)
        assert split.test

    def test_held_out_opponent_family_split_keeps_families_disjoint(self):
        mapping = {
            "opp0": "DESTROYER:Destroyer:Low Tech",
            "opp1": "DESTROYER:Destroyer:High Tech",
            "opp2": "FRIGATE:Frigate:Low Tech",
        }

        split = held_out_opponent_family_split(
            _split_rows(), mapping, holdout_fraction=0.34, seed=1
        )

        train_families = {mapping[row.opponent_variant_id] for row in split.train}
        test_families = {mapping[row.opponent_variant_id] for row in split.test}
        assert train_families.isdisjoint(test_families)

    def test_opponent_group_split_requires_complete_mapping(self):
        with pytest.raises(ValueError, match="missing an opponent hull group"):
            held_out_opponent_hull_split(
                _split_rows(), {"opp0": "enforcer"}, holdout_fraction=0.34, seed=1
            )

    def test_held_out_replicate_split_keeps_replicate_groups_disjoint(self):
        rows = [
            HonestEvalMatchupRow("p", f"id{i % 4}", f"b{i % 4}", f"opp{i % 3}", i % 2, float(i))
            for i in range(12)
        ]
        split = held_out_replicate_split(rows, holdout_fraction=0.25, seed=1)
        train_groups = {(row.build_key, row.opponent_variant_id) for row in split.train}
        test_groups = {(row.build_key, row.opponent_variant_id) for row in split.test}
        assert train_groups.isdisjoint(test_groups)

    def test_burned_split_seeds_names_seed_seventeen(self):
        assert BURNED_SPLIT_SEEDS == frozenset({17})

    def test_component_vocabulary_is_slot_agnostic_weapons_and_hullmods(self):
        build = Build(
            "hammerhead",
            {"WS 001": "heavyac", "WS 002": "heavyac", "WS 003": None},
            frozenset({"fluxcoil"}),
            4,
            2,
        )
        vocab = component_vocabulary(build)
        assert vocab == ("hullmod:fluxcoil", "weapon:heavyac")
        assert not any(token.startswith(("hull:", "flux")) for token in vocab)

    def test_component_vocabulary_split_holds_out_components_entirely(self):
        rows = _split_rows()
        build_lookup = {
            f"b{i}": Build(
                "hammerhead",
                {"WS 001": f"weapon{i}"},
                frozenset({f"mod{i}"}),
                0,
                0,
            )
            for i in range(4)
        }
        result = held_out_component_vocabulary_split(
            rows, build_lookup, holdout_fraction=0.25, max_overshoot_fraction=0.5, seed=1
        )
        assert isinstance(result, ComponentVocabularySplit)
        held_out = set(result.held_out_components)
        assert held_out
        assert result.realized_test_fraction >= 0.25
        for row in result.split.train:
            assert held_out.isdisjoint(
                component_vocabulary(build_lookup[_as_training_row(row).build_key])
            )
        for row in result.split.test:
            assert held_out & set(
                component_vocabulary(build_lookup[_as_training_row(row).build_key])
            )

    def test_component_vocabulary_split_is_deterministic(self):
        rows = _split_rows()
        build_lookup = {
            f"b{i}": Build("hammerhead", {"WS 001": f"weapon{i}"}, frozenset(), 0, 0)
            for i in range(4)
        }
        a = held_out_component_vocabulary_split(
            rows, build_lookup, holdout_fraction=0.25, max_overshoot_fraction=0.5, seed=3
        )
        b = held_out_component_vocabulary_split(
            rows, build_lookup, holdout_fraction=0.25, max_overshoot_fraction=0.5, seed=3
        )
        assert a == b

    def test_component_vocabulary_split_overshoot_bound_raises(self):
        # One component covers 3 of 4 builds: the only possible first pick
        # jumps realized fraction to 0.75 > 0.25 + 0.1.
        rows = _split_rows()
        build_lookup = {
            f"b{i}": Build(
                "hammerhead",
                {"WS 001": "shared" if i < 3 else None},
                frozenset(),
                0,
                0,
            )
            for i in range(4)
        }
        with pytest.raises(ValueError, match="overshoot"):
            held_out_component_vocabulary_split(
                rows, build_lookup, holdout_fraction=0.25, max_overshoot_fraction=0.1, seed=1
            )

    def test_component_vocabulary_split_exhaustion_raises(self):
        # Only 1 of 4 builds has any component; 25% of rows can never reach 50%.
        rows = _split_rows()
        build_lookup = {
            f"b{i}": Build(
                "hammerhead",
                {"WS 001": "solo" if i == 0 else None},
                frozenset(),
                0,
                0,
            )
            for i in range(4)
        }
        with pytest.raises(ValueError, match="exhaust"):
            held_out_component_vocabulary_split(
                rows, build_lookup, holdout_fraction=0.5, max_overshoot_fraction=0.5, seed=1
            )

    def test_component_vocabulary_split_empty_train_raises(self):
        rows = _split_rows()
        build_lookup = {
            f"b{i}": Build("hammerhead", {"WS 001": "common"}, frozenset(), 0, 0) for i in range(4)
        }
        with pytest.raises(ValueError, match="empty"):
            held_out_component_vocabulary_split(
                rows, build_lookup, holdout_fraction=0.25, max_overshoot_fraction=1.0, seed=1
            )

    def test_held_out_seed_cell_split_keeps_cell_seed_groups_disjoint(self):
        rows = [
            TrainingMatchupRow(
                "p", f"c{i % 2}", i % 3, i, f"b{i % 4}", f"opp{i % 3}", i, float(i), "finalized"
            )
            for i in range(12)
        ]
        split = held_out_seed_cell_split(rows, holdout_fraction=0.25, seed=1)
        train_groups = {(r.campaign, r.seed) for r in map(_as_training_row, split.train)}
        test_groups = {(r.campaign, r.seed) for r in map(_as_training_row, split.test)}
        assert train_groups.isdisjoint(test_groups)

    def test_forward_time_split_orders_by_trial_number(self):
        rows = list(reversed(_split_rows()))
        split = forward_time_split(rows, train_fraction=0.5)
        assert max(r.trial_number for r in map(_as_training_row, split.train)) < min(
            r.trial_number for r in map(_as_training_row, split.test)
        )

    def test_invalid_fraction_raises(self):
        with pytest.raises(ValueError):
            held_out_build_split(_split_rows(), holdout_fraction=1.0, seed=1)
        with pytest.raises(ValueError):
            forward_time_split(_split_rows(), train_fraction=0.0)


class TestGroupedKfold:
    def test_folds_partition_groups_and_rows(self):
        rows = _split_rows()
        groups = [row.build_key for row in rows]
        folds = grouped_kfold(rows, groups, n_folds=2, seed=1)
        assert len(folds) == 2
        validation_groups = []
        for fold in folds:
            train_groups = {row.build_key for row in fold.train}
            test_groups = {row.build_key for row in fold.test}
            assert train_groups.isdisjoint(test_groups)
            assert len(fold.train) + len(fold.test) == len(rows)
            validation_groups.append(test_groups)
        assert set().union(*validation_groups) == set(groups)
        assert validation_groups[0].isdisjoint(validation_groups[1])

    def test_deterministic_under_seed(self):
        rows = _split_rows()
        groups = [row.build_key for row in rows]
        assert grouped_kfold(rows, groups, 2, seed=5) == grouped_kfold(rows, groups, 2, seed=5)

    def test_too_few_groups_returns_empty(self):
        rows = _split_rows()
        groups = ["same"] * len(rows)
        assert grouped_kfold(rows, groups, 2, seed=1) == ()

    def test_fewer_than_two_folds_raises(self):
        rows = _split_rows()
        with pytest.raises(ValueError):
            grouped_kfold(rows, [row.build_key for row in rows], 1, seed=1)


class TestComponentVocabularyError:
    def test_degenerate_draws_raise_dedicated_exception(self):
        from starsector_optimizer.phase7_matchup_data import ComponentVocabularyError

        rows = _split_rows()
        build_lookup = {
            f"b{i}": Build("hammerhead", {"WS 001": "common"}, frozenset(), 0, 0) for i in range(4)
        }
        with pytest.raises(ComponentVocabularyError):
            held_out_component_vocabulary_split(
                rows, build_lookup, holdout_fraction=0.25, max_overshoot_fraction=1.0, seed=1
            )
        # Config errors stay plain ValueError, NOT the draw-failure subclass.
        with pytest.raises(ValueError) as excinfo:
            held_out_component_vocabulary_split(
                rows, build_lookup, holdout_fraction=1.5, max_overshoot_fraction=0.1, seed=1
            )
        assert not isinstance(excinfo.value, ComponentVocabularyError)

    def test_vocabulary_restricted_to_builds_in_rows(self):
        rows = _split_rows()  # builds b0..b3
        build_lookup = {
            f"b{i}": Build("hammerhead", {"WS 001": f"weapon{i}"}, frozenset(), 0, 0)
            for i in range(4)
        }
        # An extra lookup-only build must not contribute vocabulary.
        build_lookup["orphan"] = Build("hammerhead", {"WS 001": "orphan_weapon"}, frozenset(), 0, 0)
        result = held_out_component_vocabulary_split(
            rows, build_lookup, holdout_fraction=0.25, max_overshoot_fraction=0.5, seed=1
        )
        assert "weapon:orphan_weapon" not in result.held_out_components


class TestSplitPartitionSha256:
    def test_digest_is_64_hex_and_row_order_invariant(self):
        rows = _split_rows()
        split = SplitIds(train=tuple(rows[:8]), test=tuple(rows[8:]))
        shuffled = SplitIds(train=tuple(reversed(rows[:8])), test=tuple(reversed(rows[8:])))
        digest = split_partition_sha256(split)
        assert len(digest) == 64
        assert all(char in "0123456789abcdef" for char in digest)
        assert digest == split_partition_sha256(shuffled)

    def test_distinct_partitions_produce_distinct_digests(self):
        rows = _split_rows()
        split_a = SplitIds(train=tuple(rows[:8]), test=tuple(rows[8:]))
        split_b = SplitIds(train=tuple(rows[:7]), test=tuple(rows[7:]))
        assert split_partition_sha256(split_a) != split_partition_sha256(split_b)

    def test_digest_supports_honest_eval_rows(self):
        rows = [
            HonestEvalMatchupRow("p", f"id{i}", f"b{i}", f"opp{i % 2}", 0, float(i))
            for i in range(4)
        ]
        digest = split_partition_sha256(SplitIds(train=tuple(rows[:2]), test=tuple(rows[2:])))
        assert len(digest) == 64


class TestSplitSeedExclusions:
    def test_exclusion_table_names_component_vocab_149(self):
        assert SPLIT_SEED_EXCLUSIONS == {"component-vocab": frozenset({149})}

    def test_reject_excluded_split_seed_raises_for_excluded_pair(self):
        with pytest.raises(ValueError, match="excluded"):
            reject_excluded_split_seed("component-vocab", 149)

    def test_reject_excluded_split_seed_passes_other_pairs(self):
        reject_excluded_split_seed("component-vocab", 107)
        reject_excluded_split_seed("build", 149)

    def test_preflight_statuses_are_not_worker_statuses(self):
        assert DUPLICATE_SPLIT_STATUS == "duplicate_realized_split"
        assert STALE_EXCLUSION_STATUS == "stale_split_seed_exclusion"
        assert DUPLICATE_SPLIT_STATUS not in INSUFFICIENCY_STATUSES
        assert STALE_EXCLUSION_STATUS not in INSUFFICIENCY_STATUSES
