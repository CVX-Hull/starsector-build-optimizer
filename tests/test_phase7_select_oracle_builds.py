"""Tests for the rank-stratified oracle-coverage selector (pure core).

The CatBoost fit path (predicted_scores_for_cell) is exercised operationally at
selection time on the real frozen DB (its determinism is CatBoost's own under
random_seed=23 + thread_count=1); these unit tests pin the deterministic
ranking / stratification / pick rules and the JSON provenance contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from starsector_optimizer.models import Build

_SPEC = importlib.util.spec_from_file_location(
    "phase7_select_oracle_builds",
    Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "phase7_select_oracle_builds.py",
)
assert _SPEC is not None and _SPEC.loader is not None
sel = importlib.util.module_from_spec(_SPEC)
sys.modules["phase7_select_oracle_builds"] = sel  # dataclass introspection needs this
_SPEC.loader.exec_module(sel)


def _build(vents: int) -> Build:
    # Distinct builds differ by a scalar field; content is irrelevant to the pure core.
    return Build(
        hull_id="hammerhead",
        weapon_assignments={},
        hullmods=frozenset(),
        flux_vents=vents,
        flux_capacitors=0,
    )


def _scored(scores: dict[str, float]) -> dict:  # values are sel.ScoreInfo (dynamically loaded)
    return {
        bk: sel.ScoreInfo(predicted_score=s, trial_number=i, build=_build(i))
        for i, (bk, s) in enumerate(scores.items())
    }


class TestMedianIndex:
    @pytest.mark.parametrize(
        "n,expected",
        [(1, 0), (2, 0), (3, 1), (4, 1), (5, 2), (6, 2), (7, 3)],
    )
    def test_lower_middle_on_even(self, n: int, expected: int) -> None:
        assert sel._median_index(n) == expected


class TestStratifyIndices:
    def test_equal_count_contiguous_tertiles(self) -> None:
        # 9 items → [0,1,2],[3,4,5],[6,7,8]
        assert sel._stratify_indices(9) == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]

    def test_near_equal_when_not_divisible(self) -> None:
        # numpy.array_split(10, 3) → sizes 4,3,3 (larger groups first)
        groups = sel._stratify_indices(10)
        assert [len(g) for g in groups] == [4, 3, 3]
        assert groups[0] == [0, 1, 2, 3]

    def test_minimum_three(self) -> None:
        assert [len(g) for g in sel._stratify_indices(3)] == [1, 1, 1]

    def test_fewer_than_three_raises(self) -> None:
        with pytest.raises(ValueError, match="distinct builds"):
            sel._stratify_indices(2)


class TestRankStratifyPick:
    def test_picks_median_of_each_tertile(self) -> None:
        # 9 builds, scores 0..8 keyed so lexical order matches score order.
        scores = {f"b{i}": float(i) for i in range(9)}
        picked = sel.rank_stratify_pick(_scored(scores), campaign="c", seed=100)
        assert [p.stratum for p in picked] == ["bottom", "middle", "top"]
        assert [p.source_rank for p in picked] == [1, 2, 3]
        # tertiles [0,1,2],[3,4,5],[6,7,8]; median index within each = 1 → global 1,4,7
        assert [p.predicted_rank_in_cell for p in picked] == [2, 5, 8]
        assert [p.build_key for p in picked] == ["b1", "b4", "b7"]

    def test_source_rank_unique_within_cell(self) -> None:
        picked = sel.rank_stratify_pick(
            _scored({f"b{i}": float(i) for i in range(12)}), campaign="c", seed=1
        )
        assert len({p.source_rank for p in picked}) == sel.TERTILE_COUNT

    def test_all_equal_scores_still_picks_three(self) -> None:
        # Degenerate: every predicted score identical → tie-break on build_key,
        # array_split still yields 3 non-empty groups.
        picked = sel.rank_stratify_pick(
            _scored({f"b{i}": 0.5 for i in range(9)}), campaign="c", seed=2
        )
        assert len(picked) == sel.TERTILE_COUNT
        assert [p.build_key for p in picked] == ["b1", "b4", "b7"]  # lexical tie-break

    def test_tie_break_is_score_then_build_key(self) -> None:
        # Two builds share a score; the lexically smaller build_key ranks first.
        scored = _scored({"bbb": 1.0, "aaa": 1.0, "ccc": 2.0, "ddd": 3.0, "eee": 4.0, "fff": 5.0})
        picked = sel.rank_stratify_pick(scored, campaign="c", seed=3)
        # sorted: aaa(1) bbb(1) ccc(2) | ddd(3) eee(4) | ... wait n=6 → [0,1],[2,3],[4,5]
        # medians (lower-middle of size2 = idx0): global 0,2,4 → aaa, ccc, eee
        assert [p.build_key for p in picked] == ["aaa", "ccc", "eee"]

    def test_fewer_than_three_builds_raises(self) -> None:
        with pytest.raises(ValueError):
            sel.rank_stratify_pick(_scored({"a": 1.0, "b": 2.0}), campaign="c", seed=4)

    def test_indices_and_ranks_are_native_ints(self) -> None:
        # np.array_split yields np.int64; the payload must be JSON-serializable.
        import json

        picked = sel.rank_stratify_pick(
            _scored({f"b{i}": float(i) for i in range(9)}), campaign="c", seed=1
        )
        for p in picked:
            assert type(p.predicted_rank_in_cell) is int
            assert type(p.source_rank) is int
        # _stratify_indices elements must be native int too.
        assert all(type(i) is int for group in sel._stratify_indices(10) for i in group)
        json.dumps({"rank": picked[0].predicted_rank_in_cell})  # must not raise

    def test_deterministic_across_runs(self) -> None:
        scored = _scored({f"b{i}": float((i * 7) % 11) for i in range(15)})
        a = sel.rank_stratify_pick(scored, campaign="c", seed=5)
        b = sel.rank_stratify_pick(scored, campaign="c", seed=5)
        assert [(p.build_key, p.source_rank) for p in a] == [
            (p.build_key, p.source_rank) for p in b
        ]


class TestConstants:
    def test_hpo_seed_and_thread_count(self) -> None:
        assert sel.SELECTOR_HPO_SEED == 23
        assert sel.SELECTOR_THREAD_COUNT == 1
        assert sel.TERTILE_COUNT == 3
        assert sel.STRATUM_ORDINALS == {"bottom": 1, "middle": 2, "top": 3}


class TestPredictedScoresForCell:
    """Grouping/provenance logic (audit MEDIUM): mock the featurizer+model so the
    dedup-by-build_key + min-trial + opponent-panel-mean is pinned without a real
    CatBoost fit."""

    def test_dedup_min_trial_and_panel_mean(self, monkeypatch):
        import numpy as np
        from starsector_optimizer.phase7_matchup_data import TrainingMatchupRow

        rows = [
            TrainingMatchupRow("p", "c", 100, 5, "A", "opp1", 0, 0.0, "completed"),
            TrainingMatchupRow(
                "p", "c", 100, 2, "A", "opp2", 1, 0.0, "completed"
            ),  # A again, lower trial
            TrainingMatchupRow("p", "c", 100, 7, "B", "opp1", 0, 0.0, "completed"),
        ]
        bundle = sel.baseline.FeatureBundle(
            rows=tuple(rows), records=tuple({} for _ in rows), targets=np.zeros(len(rows))
        )
        monkeypatch.setattr(sel.baseline, "_feature_bundle", lambda *a, **k: bundle)

        class _FakeModel:
            def fit(self, *a):
                return None

            def predict(self, *a):
                return sel.learned.PredictionResult(np.array([1.0, 3.0, 5.0]), {})

        monkeypatch.setattr(sel.learned, "make_model", lambda *a, **k: _FakeModel())
        monkeypatch.setattr(sel._replay, "force_deterministic_predict", lambda m: None)

        scores = sel.predicted_scores_for_cell(rows, {"A": _build(0), "B": _build(1)}, None)
        assert scores["A"] == (2.0, 2)  # mean(1,3)=2.0, min(5,2)=2
        assert scores["B"] == (5.0, 7)


class TestSelectOracleBuildsAssembly:
    """End-to-end assembly with predicted_scores_for_cell mocked (audit MEDIUM):
    per-cell filtering / isolation + 3-per-cell + distinct keys + JSON shape."""

    def test_per_cell_isolation_and_shape(self, monkeypatch, tmp_path):
        from starsector_optimizer.phase7_matchup_data import (
            BuildSourceKind,
            RecoveredBuild,
            TrainingMatchupRow,
        )

        cells = {100: ["a1", "a2", "a3"], 101: ["b1", "b2", "b3"]}
        rows = [
            TrainingMatchupRow("p", "accounting-hammerhead", seed, i, k, "opp", 0, 0.0, "completed")
            for seed, keys in cells.items()
            for i, k in enumerate(keys)
        ]
        recovered = tuple(
            RecoveredBuild(
                k,
                _build(i),
                BuildSourceKind.EXACT_LOGGED_BUILD,
                "accounting-hammerhead",
                "s",
                seed,
                None,
                i,
                None,
                "p",
            )
            for seed, keys in cells.items()
            for i, k in enumerate(keys)
        )
        monkeypatch.setattr(sel, "load_training_matchups", lambda db: tuple(rows))
        monkeypatch.setattr(sel, "load_recovered_builds", lambda db: recovered)
        monkeypatch.setattr(sel, "_baseline_config", lambda db, gd: None)
        monkeypatch.setattr(
            sel,
            "predicted_scores_for_cell",
            lambda cell_rows, bl, cfg: {
                r.build_key: (float(i), r.trial_number) for i, r in enumerate(cell_rows)
            },
        )

        payload = sel.select_oracle_builds(
            frozen_db=tmp_path / "x.db",
            game_dir=tmp_path,
            campaign="accounting-hammerhead",
            seeds=(100, 101),
            prereg_commit="testcommit",
        )
        assert payload["schema_version"] == 1
        assert payload["prereg_commit"] == "testcommit"
        assert len(payload["builds"]) == 6
        from collections import Counter

        assert dict(Counter(b["source_seed_idx"] for b in payload["builds"])) == {100: 3, 101: 3}
        # no cross-cell key leakage
        s100 = {b["build_key"] for b in payload["builds"] if b["source_seed_idx"] == 100}
        s101 = {b["build_key"] for b in payload["builds"] if b["source_seed_idx"] == 101}
        assert s100 <= {"a1", "a2", "a3"}
        assert s101 <= {"b1", "b2", "b3"}
        # every build entry round-trips to a valid canonical build dict
        assert all(
            set(b["build"]) >= {"hull_id", "weapon_assignments", "hullmods"}
            for b in payload["builds"]
        )

    def test_missing_cell_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sel, "load_training_matchups", lambda db: ())
        monkeypatch.setattr(sel, "load_recovered_builds", lambda db: ())
        monkeypatch.setattr(sel, "_baseline_config", lambda db, gd: None)
        with pytest.raises(ValueError, match="no training_matchups rows"):
            sel.select_oracle_builds(
                frozen_db=tmp_path / "x.db",
                game_dir=tmp_path,
                campaign="accounting-hammerhead",
                seeds=(100,),
                prereg_commit="t",
            )
