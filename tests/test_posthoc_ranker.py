"""Tests for `posthoc_ranker` — recover synthetic top-K under opponent
confounding, asymmetric sample sizes, and TIMEOUT mixing.

Each test constructs records with a known build skill ordering and
opponent strength bias, then asserts the ranker recovers the top-3.
Bradley–Terry only sees winner labels; TWFE/EB see hp_differential.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from starsector_optimizer.posthoc_ranker import (
    BradleyTerryConfig,
    TrialRecord,
    _BuildId,
    load_records,
    rank_bradley_terry,
    rank_raw_mean,
    rank_twfe,
    rank_twfe_eb,
    spearman_rho,
    topk_overlap,
)


def _bid(label: str) -> _BuildId:
    return _BuildId(
        hull_id="hammerhead",
        weapons=(("WS_001", label),),
        hullmods=(),
        flux_vents=0,
        flux_capacitors=0,
    )


def _make_records(
    *,
    n_builds: int = 30,
    n_opps: int = 8,
    samples_per_build: int = 6,
    seed: int = 0,
) -> tuple[list[TrialRecord], np.ndarray, np.ndarray]:
    """Synthetic confounded data.

    Build skill α_i ~ Uniform(-1, 1); opponent strength β_j ~ Uniform(-0.5, 0.5).
    Each build samples `samples_per_build` opponents WITHOUT replacement biased
    toward the easier opponents (pruner-like skewed assignment), generates
    hp_diff = clip(α - β + noise, -1, 1).
    """
    rng = np.random.default_rng(seed)
    alpha_true = rng.uniform(-1.0, 1.0, size=n_builds)
    beta_true = rng.uniform(-0.5, 0.5, size=n_opps)
    records = []
    # Easy-opponent bias: weight ∝ exp(-2*β) so easier opps get more matches.
    p_easy = np.exp(-2 * beta_true)
    p_easy /= p_easy.sum()
    for i in range(n_builds):
        opp_picks = rng.choice(n_opps, size=samples_per_build, replace=False, p=p_easy)
        matches = []
        for j in opp_picks:
            score = alpha_true[i] - beta_true[j] + rng.normal(0, 0.15)
            score = float(np.clip(score, -1.0, 1.0))
            if score > 0.1:
                winner = "PLAYER"
            elif score < -0.1:
                winner = "ENEMY"
            else:
                winner = "TIMEOUT"
            matches.append((f"opp{j}", score, winner))
        records.append(
            TrialRecord(
                study="synth/seed0",
                trial_number=i,
                build_id=_bid(f"b{i}"),
                raw_build={"id": f"b{i}"},
                matches=tuple(matches),
            )
        )
    return records, alpha_true, beta_true


# -------------------------- raw mean: biased baseline ----------------------


class TestRawMean:
    def test_returns_top_k(self):
        records, _, _ = _make_records()
        out = rank_raw_mean(records, k=5)
        assert len(out) == 5
        assert all(out[i].score >= out[i + 1].score for i in range(4))

    def test_pools_matches_across_studies(self):
        records, _, _ = _make_records(n_builds=3, samples_per_build=4)
        # Duplicate the records under a second study label; ranking should pool.
        rec_a = records[0]
        rec_b = TrialRecord(
            study="synth/seed1",
            trial_number=99,
            build_id=rec_a.build_id,
            raw_build=rec_a.raw_build,
            matches=rec_a.matches,
        )
        out = rank_raw_mean([rec_a, rec_b], k=1)
        # Total matches doubled by pooling.
        assert out[0].n_matches == 2 * len(rec_a.matches)
        assert "synth/seed0" in out[0].studies
        assert "synth/seed1" in out[0].studies


# ------------------------- TWFE: deconfounded ranking ----------------------


class TestTWFE:
    def test_recovers_alpha_ordering_better_than_raw_mean(self):
        records, alpha_true, _ = _make_records(seed=1)
        # Compare each method's top-5 to the true top-5.
        true_top5 = set(np.argsort(-alpha_true)[:5])
        true_ids = {_bid(f"b{i}") for i in true_top5}
        raw = {r.build_id for r in rank_raw_mean(records, k=5)}
        twfe = {r.build_id for r in rank_twfe(records, k=5)}
        # Deconfounded TWFE should overlap as much or more with truth than raw.
        assert len(twfe & true_ids) >= len(raw & true_ids)

    def test_handles_partial_observations(self):
        # Some opponents observed 1×, others 5×.
        records, _, _ = _make_records(samples_per_build=3, n_opps=5, seed=2)
        out = rank_twfe(records, k=3)
        assert len(out) == 3
        # n_matches should equal samples_per_build for every build.
        for r in out:
            assert r.n_matches == 3


# --------------------------- TWFE + EB: shrinkage --------------------------


class TestTWFEEB:
    def test_eb_shrinks_extreme_values_toward_mean(self):
        records, _, _ = _make_records(seed=3, samples_per_build=3)
        twfe_out = rank_twfe(records, k=30)
        eb_out = rank_twfe_eb(records, k=30)
        # The range of scores should not increase under EB.
        twfe_range = twfe_out[0].score - twfe_out[-1].score
        eb_range = eb_out[0].score - eb_out[-1].score
        assert eb_range <= twfe_range + 1e-9

    def test_eb_top_k_matches_truth(self):
        records, alpha_true, _ = _make_records(seed=4, n_builds=40)
        true_top5 = {_bid(f"b{i}") for i in np.argsort(-alpha_true)[:5]}
        eb_top5 = {r.build_id for r in rank_twfe_eb(records, k=5)}
        # Reasonable lower bound on recovery — at least 2/5 in top-5.
        assert len(eb_top5 & true_top5) >= 2


# -------------------------- Bradley-Terry: skill ---------------------------


class TestBradleyTerry:
    def test_recovers_alpha_ordering(self):
        records, alpha_true, _ = _make_records(seed=5, n_builds=30, samples_per_build=8)
        true_top5 = {_bid(f"b{i}") for i in np.argsort(-alpha_true)[:5]}
        bt_top5 = {r.build_id for r in rank_bradley_terry(records, k=5)}
        # BT under heavy confounding + only winner labels: recovers >= 2/5.
        assert len(bt_top5 & true_top5) >= 2

    def test_returns_finite_uncertainty(self):
        records, _, _ = _make_records(seed=6, n_builds=10, samples_per_build=6)
        out = rank_bradley_terry(records, k=5)
        for r in out:
            assert np.isfinite(r.sigma) and r.sigma > 0
            assert r.n_matches > 0

    def test_timeout_weighted_as_draw(self):
        # All-timeout records should produce α ≈ 0 (no wins or losses).
        bid = _bid("t1")
        rec = TrialRecord(
            study="synth/x",
            trial_number=0,
            build_id=bid,
            raw_build={},
            matches=tuple(("opp0", 0.0, "TIMEOUT") for _ in range(6)),
        )
        out = rank_bradley_terry([rec], k=1, config=BradleyTerryConfig(ridge=0.5))
        assert abs(out[0].score) < 0.5  # near zero


# ------------------------------ comparison helpers -------------------------


class TestComparisonHelpers:
    def test_overlap_and_spearman(self):
        records, _, _ = _make_records(seed=7)
        a = rank_raw_mean(records, k=5)
        b = rank_twfe_eb(records, k=5)
        ov = topk_overlap(a, b)
        assert 0 <= ov <= 5
        rho = spearman_rho(a, b)
        assert -1.0 <= rho <= 1.0 or np.isnan(rho)


# --------------------------- load_records on JSONL -------------------------


class TestLoadRecords:
    def test_skips_pruned_cache_invalid(self, tmp_path: Path):
        cell_dir = tmp_path / "wave1-c1" / "hammerhead__early__tpe__seed0"
        cell_dir.mkdir(parents=True)
        log = cell_dir / "evaluation_log.jsonl"

        def _row(**over):
            base = {
                "trial_number": 1,
                "build": {
                    "hull_id": "hammerhead",
                    "weapon_assignments": {"WS_001": "lightac"},
                    "hullmods": ["heavyarmor"],
                    "flux_vents": 1,
                    "flux_capacitors": 0,
                },
                "opponent_results": [
                    {
                        "opponent": "berserker_Assault",
                        "winner": "PLAYER",
                        "duration_seconds": 30.0,
                        "hp_differential": 0.5,
                    },
                ],
                "pruned": False,
                "cache_hit": False,
                "invalid_spec": False,
            }
            base.update(over)
            return json.dumps(base) + "\n"

        log.write_text(
            _row(trial_number=1)
            + _row(trial_number=2, pruned=True)
            + _row(trial_number=3, cache_hit=True)
            + _row(trial_number=4, invalid_spec=True)
            + _row(trial_number=5, opponent_results=[]),  # empty matches → skipped
        )
        recs = load_records([log])
        assert len(recs) == 1
        assert recs[0].trial_number == 1
        assert recs[0].study == "c1/seed0"
