"""Tests for Phase 7 prior-run build recovery and materialization."""

import json
import sqlite3
from pathlib import Path

import pytest

from starsector_optimizer.models import Build
from starsector_optimizer.repair import is_feasible
from starsector_optimizer.phase7_matchup_data import (
    BuildSourceKind,
    HonestEvalMatchupRow,
    RecoveredBuild,
    TrainingMatchupRow,
    build_from_log_row,
    build_key,
    forward_time_split,
    held_out_component_combination_split,
    held_out_build_split,
    held_out_opponent_split,
    held_out_replicate_split,
    held_out_seed_cell_split,
    iter_honest_eval_matchups,
    iter_training_matchups,
    materialize_sqlite,
    recover_honest_eval_candidate_builds,
    recover_logged_builds,
    recover_study_db_builds,
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
        "insert into trial_values (trial_id, objective, value, value_type) values (1, 0, 0.25, 'FINITE')"
    )
    params = [
        ("weapon_WS 001", 1.0, {"name": "CategoricalDistribution", "attributes": {"choices": ["empty", "heavyac"]}}),
        ("weapon_WS 002", 1.0, {"name": "CategoricalDistribution", "attributes": {"choices": ["empty", "heavymortar"]}}),
        ("hullmod_fluxcoil", 0.0, {"name": "CategoricalDistribution", "attributes": {"choices": [True, False]}}),
        ("flux_vents", 4.0, {"name": "IntDistribution", "attributes": {"low": 0, "high": 30, "step": 1, "log": False}}),
        ("flux_capacitors", 2.0, {"name": "IntDistribution", "attributes": {"low": 0, "high": 30, "step": 1, "log": False}}),
    ]
    if unsupported:
        params.append(("bad_param", 0.0, {"name": "UnsupportedDistribution", "attributes": {}}))
    for idx, (name, value, dist) in enumerate(params, start=1):
        con.execute(
            "insert into trial_params (param_id, trial_id, param_name, param_value, distribution_json) values (?, 1, ?, ?, ?)",
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
            recover_study_db_builds(
                db_path, game_data.hulls["hammerhead"], game_data, manifest
            )


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
        path.write_text(json.dumps({
            "schema_version": 1,
            "matchup_id": "m",
            "build_id": "honest__wave1-c0a__s0__seed0__rank1",
            "opponent_variant_id": "enforcer_Balanced",
            "replicate_idx": 2,
            "fitness": 0.75,
            "completed_at": "2026-05-11T00:00:00+00:00",
        }) + "\n")

        rows = list(iter_honest_eval_matchups(
            path, {"honest__wave1-c0a__s0__seed0__rank1": "abc"}
        ))

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
        assert con.execute("select source_kind from recovered_builds").fetchone()[0] == "exact_logged_build"


def _split_rows() -> list[TrainingMatchupRow]:
    return [
        TrainingMatchupRow("p", "c", 0, i, f"b{i % 4}", f"opp{i % 3}", i, float(i), "finalized")
        for i in range(12)
    ]


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

    def test_held_out_replicate_split_keeps_replicate_groups_disjoint(self):
        rows = [
            HonestEvalMatchupRow("p", f"id{i % 4}", f"b{i % 4}", f"opp{i % 3}", i % 2, float(i))
            for i in range(12)
        ]
        split = held_out_replicate_split(rows, holdout_fraction=0.25, seed=1)
        train_groups = {
            (row.build_key, row.opponent_variant_id)
            for row in split.train
        }
        test_groups = {
            (row.build_key, row.opponent_variant_id)
            for row in split.test
        }
        assert train_groups.isdisjoint(test_groups)

    def test_held_out_component_combination_split_keeps_components_disjoint(self):
        rows = _split_rows()
        build_lookup = {
            f"b{i}": Build(
                "hammerhead",
                {"WS 001": "heavyac" if i % 2 == 0 else "heavymortar"},
                frozenset({"fluxcoil"} if i < 2 else {"armoredweapons"}),
                0,
                0,
            )
            for i in range(4)
        }
        split = held_out_component_combination_split(
            rows, build_lookup, holdout_fraction=0.25, seed=1
        )
        train_builds = {row.build_key for row in split.train}
        test_builds = {row.build_key for row in split.test}
        assert train_builds.isdisjoint(test_builds)

    def test_held_out_seed_cell_split_keeps_cell_seed_groups_disjoint(self):
        rows = [
            TrainingMatchupRow("p", f"c{i % 2}", i % 3, i, f"b{i % 4}", f"opp{i % 3}", i, float(i), "finalized")
            for i in range(12)
        ]
        split = held_out_seed_cell_split(rows, holdout_fraction=0.25, seed=1)
        train_groups = {(row.campaign, row.seed) for row in split.train}
        test_groups = {(row.campaign, row.seed) for row in split.test}
        assert train_groups.isdisjoint(test_groups)

    def test_forward_time_split_orders_by_trial_number(self):
        rows = list(reversed(_split_rows()))
        split = forward_time_split(rows, train_fraction=0.5)
        assert max(row.trial_number for row in split.train) < min(
            row.trial_number for row in split.test
        )

    def test_invalid_fraction_raises(self):
        with pytest.raises(ValueError):
            held_out_build_split(_split_rows(), holdout_fraction=1.0, seed=1)
        with pytest.raises(ValueError):
            forward_time_split(_split_rows(), train_fraction=0.0)
