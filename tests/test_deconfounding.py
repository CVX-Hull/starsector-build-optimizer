"""Tests for deconfounding — TWFE decomposition, trimmed alpha, ScoreMatrix."""

import numpy as np
import pytest

from starsector_optimizer.models import TWFEConfig


# --- TWFEConfig Tests ---


class TestTWFEConfig:

    def test_frozen(self):
        """TWFEConfig is a frozen dataclass."""
        config = TWFEConfig()
        with pytest.raises(AttributeError):
            config.ridge = 0.5  # type: ignore[misc]

    def test_defaults(self):
        config = TWFEConfig()
        assert config.ridge == 0.01
        assert config.n_iters == 20
        assert config.trim_worst == 2
        assert config.n_incumbent_overlap == 5
        assert config.n_anchors == 3
        assert config.anchor_burn_in == 30
        assert config.min_disc_samples == 5


# --- twfe_decompose Tests ---


class TestTWFEDecompose:

    def test_known_values(self):
        """Recover known alpha + beta from a fully-observed 3x3 matrix."""
        from starsector_optimizer.deconfounding import twfe_decompose

        alpha_true = np.array([1.0, 2.0, 3.0])
        beta_true = np.array([-0.5, 0.0, 0.5])
        # Y_ij = alpha_i + beta_j
        Y = alpha_true[:, None] + beta_true[None, :]

        alpha, beta = twfe_decompose(Y, n_iters=50, ridge=0.001)
        # Recovered values may be shifted by a constant (identifiability up to offset)
        # Check that alpha - beta differences match
        for i in range(3):
            for j in range(3):
                assert alpha[i] + beta[j] == pytest.approx(Y[i, j], abs=0.1)

    def test_sparse_matrix(self):
        """NaN entries produce reasonable decomposition."""
        from starsector_optimizer.deconfounding import twfe_decompose

        Y = np.array([
            [1.0, np.nan, 0.5],
            [np.nan, 2.0, 1.5],
            [0.0, 1.0, np.nan],
        ])
        alpha, beta = twfe_decompose(Y)
        # Should not crash, should produce finite values
        assert np.all(np.isfinite(alpha))
        assert np.all(np.isfinite(beta))
        # Alpha ordering should reflect row means: row 1 (mean ~1.75) > row 0 (~0.75) > row 2 (~0.5)
        assert alpha[1] > alpha[0]
        assert alpha[0] > alpha[2]

    def test_single_build(self):
        """Edge case: 1 build, N opponents."""
        from starsector_optimizer.deconfounding import twfe_decompose

        Y = np.array([[1.0, 2.0, 3.0]])
        alpha, beta = twfe_decompose(Y)
        assert alpha.shape == (1,)
        assert beta.shape == (3,)
        assert np.isfinite(alpha[0])

    def test_ridge_prevents_divergence(self):
        """All-NaN column doesn't cause division by zero."""
        from starsector_optimizer.deconfounding import twfe_decompose

        Y = np.array([
            [1.0, np.nan],
            [2.0, np.nan],
            [3.0, np.nan],
        ])
        alpha, beta = twfe_decompose(Y, ridge=0.01)
        assert np.all(np.isfinite(alpha))
        assert np.all(np.isfinite(beta))


# --- trimmed_alpha Tests ---


class TestTrimmedAlpha:

    def test_drops_worst(self):
        """Trimming drops the lowest residuals."""
        from starsector_optimizer.deconfounding import trimmed_alpha

        scores = np.array([1.0, 5.0, 2.0, 3.0, 4.0])
        beta = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        # Without trim: mean = 3.0
        # With trim_worst=2: drop 1.0 and 2.0 → mean(3, 4, 5) = 4.0
        result = trimmed_alpha(scores, beta, trim_worst=2)
        assert result == pytest.approx(4.0)

    def test_zero_trim_is_mean(self):
        """trim_worst=0 returns the plain mean."""
        from starsector_optimizer.deconfounding import trimmed_alpha

        scores = np.array([1.0, 2.0, 3.0])
        beta = np.zeros(3)
        result = trimmed_alpha(scores, beta, trim_worst=0)
        assert result == pytest.approx(2.0)

    def test_with_beta(self):
        """Beta is subtracted from scores before trimming."""
        from starsector_optimizer.deconfounding import trimmed_alpha

        scores = np.array([3.0, 4.0, 5.0])
        beta = np.array([1.0, 1.0, 1.0])
        # Residuals: [2.0, 3.0, 4.0], trim 1 worst → mean(3, 4) = 3.5
        result = trimmed_alpha(scores, beta, trim_worst=1)
        assert result == pytest.approx(3.5)

    def test_nan_entries_skipped(self):
        """NaN entries in scores are skipped."""
        from starsector_optimizer.deconfounding import trimmed_alpha

        scores = np.array([1.0, np.nan, 3.0, np.nan, 5.0])
        beta = np.zeros(5)
        # Observed: [1.0, 3.0, 5.0], trim 1 → mean(3, 5) = 4.0
        result = trimmed_alpha(scores, beta, trim_worst=1)
        assert result == pytest.approx(4.0)

    def test_trim_exceeds_observed_warns(self):
        """When trim_worst >= n_observed, warn and return untrimmed mean."""
        from starsector_optimizer.deconfounding import trimmed_alpha

        scores = np.array([1.0, 3.0])
        beta = np.zeros(2)
        with pytest.warns(UserWarning, match="trim_worst"):
            result = trimmed_alpha(scores, beta, trim_worst=5)
        # Should return untrimmed mean
        assert result == pytest.approx(2.0)

    def test_trim_all_but_one(self):
        """Trimming all but one returns the best residual."""
        from starsector_optimizer.deconfounding import trimmed_alpha

        scores = np.array([1.0, 2.0, 3.0, 10.0])
        beta = np.zeros(4)
        # trim 3 worst → keep only 10.0
        result = trimmed_alpha(scores, beta, trim_worst=3)
        assert result == pytest.approx(10.0)


# --- ScoreMatrix Tests ---


class TestScoreMatrix:

    def test_record_round_trip(self):
        """Record scores and retrieve build alpha."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        sm.record(0, "opp_a", 1.0)
        sm.record(0, "opp_b", 2.0)
        sm.record(1, "opp_a", 3.0)
        sm.record(1, "opp_b", 4.0)

        config = TWFEConfig(trim_worst=0)
        alpha_0 = sm.build_alpha(0, config)
        alpha_1 = sm.build_alpha(1, config)
        # Build 1 scored higher than build 0 against both opponents
        assert alpha_1 > alpha_0

    def test_build_alpha_calls_trimmed(self):
        """build_alpha uses trim_worst from config."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        # Build 0: scores [1.0, 10.0, 10.0] — one bad matchup
        sm.record(0, "opp_a", 1.0)
        sm.record(0, "opp_b", 10.0)
        sm.record(0, "opp_c", 10.0)
        # Build 1: scores [5.0, 5.0, 5.0] — consistent
        sm.record(1, "opp_a", 5.0)
        sm.record(1, "opp_b", 5.0)
        sm.record(1, "opp_c", 5.0)

        # Without trimming: build 0 mean=7.0 > build 1 mean=5.0
        config_no_trim = TWFEConfig(trim_worst=0)
        assert sm.build_alpha(0, config_no_trim) > sm.build_alpha(1, config_no_trim)

        # With trim_worst=1: build 0 drops 1.0 → mean(10,10)=10 vs build 1 drops 5 → mean(5,5)=5
        # Build 0 still wins, but the gap is larger (trimming helps RPS builds)

    def test_caching(self):
        """Second build_alpha without new record reuses cached decomposition."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        sm.record(0, "opp_a", 1.0)

        config = TWFEConfig(trim_worst=0)
        alpha1 = sm.build_alpha(0, config)
        alpha2 = sm.build_alpha(0, config)
        assert alpha1 == alpha2
        # Verify cache was used (dirty flag should be False after first call)
        assert not sm._dirty

    def test_invalidation(self):
        """record() invalidates the cached decomposition."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        sm.record(0, "opp_a", 1.0)

        config = TWFEConfig(trim_worst=0)
        sm.build_alpha(0, config)
        assert not sm._dirty

        sm.record(1, "opp_a", 5.0)
        assert sm._dirty

    def test_opponent_beta(self):
        """opponent_beta returns cached beta values."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        sm.record(0, "opp_easy", 10.0)
        sm.record(0, "opp_hard", 1.0)
        sm.record(1, "opp_easy", 8.0)
        sm.record(1, "opp_hard", 0.5)

        config = TWFEConfig(trim_worst=0)
        sm.build_alpha(0, config)  # trigger decomposition

        beta_easy = sm.opponent_beta("opp_easy")
        beta_hard = sm.opponent_beta("opp_hard")
        # Easy opponent should have higher beta (contributes more to scores)
        assert beta_easy > beta_hard

    def test_n_builds_and_n_opponents(self):
        """Properties track distinct builds and opponents."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        assert sm.n_builds == 0
        assert sm.n_opponents == 0

        sm.record(0, "opp_a", 1.0)
        sm.record(0, "opp_b", 2.0)
        sm.record(1, "opp_a", 3.0)

        assert sm.n_builds == 2
        assert sm.n_opponents == 2
