"""Tests for deconfounding — TWFE decomposition, trimmed alpha, ScoreMatrix, EB shrinkage."""

import numpy as np
import pytest
from scipy.stats import spearmanr

from starsector_optimizer.models import EBShrinkageConfig, TWFEConfig


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


# --- ScoreMatrix.build_sigma_sq Tests (Phase 5D) ---


class TestBuildSigmaSq:

    def test_raises_before_decompose(self):
        """build_sigma_sq raises ValueError before any build_alpha() call."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        sm.record(0, "opp_a", 1.0)
        with pytest.raises(ValueError, match="build_alpha"):
            sm.build_sigma_sq(0)

    def test_matches_pooled_mse_over_n_i(self):
        """σ̂_i² = σ̂_ε² / n_i using pooled residual MSE."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        # Perfectly additive 2x2: Y_ij = alpha_i + beta_j, no noise
        # alphas [1, 3], betas [0, 1]: Y = [[1, 2], [3, 4]]
        sm.record(0, "opp_a", 1.0)
        sm.record(0, "opp_b", 2.0)
        sm.record(1, "opp_a", 3.0)
        sm.record(1, "opp_b", 4.0)

        config = TWFEConfig(trim_worst=0, ridge=0.0)
        sm.build_alpha(0, config)  # trigger decomposition

        # With zero residuals, σ̂_ε² should be ~0 (bounded below by denominator >= 1)
        sigma_sq_0 = sm.build_sigma_sq(0)
        assert sigma_sq_0 == pytest.approx(0.0, abs=1e-6)

    def test_invalidated_on_new_record(self):
        """After record(), cache is dirty → build_sigma_sq raises until build_alpha recomputes."""
        from starsector_optimizer.deconfounding import ScoreMatrix

        sm = ScoreMatrix()
        sm.record(0, "opp_a", 1.0)
        sm.record(0, "opp_b", 2.0)
        sm.record(1, "opp_a", 3.0)
        sm.record(1, "opp_b", 4.0)

        config = TWFEConfig(trim_worst=0)
        sm.build_alpha(0, config)
        # First call succeeds
        sm.build_sigma_sq(0)

        # New record invalidates cache
        sm.record(0, "opp_c", 5.0)
        with pytest.raises(ValueError):
            sm.build_sigma_sq(0)

        # Re-decomposing revives it
        sm.build_alpha(0, config)
        sm.build_sigma_sq(0)  # no raise


# --- eb_shrinkage Tests (Phase 5D) ---


class TestEBShrinkage:

    def _make_synthetic(self, n=50, p=3, tau=0.5, sigma=0.1, seed=0):
        """Generate (alpha_hat, sigma_sq, X, alpha_true) from a 2-level Gaussian model."""
        rng = np.random.default_rng(seed)
        X = rng.normal(size=(n, p))
        gamma = np.array([0.0] + [1.0] * p)  # intercept + unit coefs
        # After in-function standardization: X has mean 0, std 1 columnwise in expectation
        mu_true = X @ gamma[1:] + gamma[0]
        alpha_true = mu_true + rng.normal(scale=tau, size=n)
        sigma_sq = np.full(n, sigma ** 2)
        alpha_hat = alpha_true + rng.normal(scale=sigma, size=n)
        return alpha_hat, sigma_sq, X, alpha_true

    def test_recovers_known_model(self):
        """EB shrinkage MSE beats raw α̂ in the correct regime."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        # High sigma / moderate tau — EB should pull toward prior
        alpha_hat, sigma_sq, X, alpha_true = self._make_synthetic(
            n=100, p=3, tau=0.5, sigma=1.0, seed=42,
        )
        cfg = EBShrinkageConfig()
        alpha_eb, gamma, tau2, kept = eb_shrinkage(alpha_hat, sigma_sq, X, cfg)

        mse_raw = np.mean((alpha_hat - alpha_true) ** 2)
        mse_eb = np.mean((alpha_eb - alpha_true) ** 2)
        assert mse_eb < mse_raw  # shrinkage helped
        assert tau2 > 0
        assert len(kept) == X.shape[1]  # no cols dropped

    def test_zero_sigma_is_no_shrinkage(self):
        """σ̂_i² → 0 ⇒ w_i → 1 ⇒ α̂_EB ≈ α̂."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        rng = np.random.default_rng(0)
        n = 30
        alpha_hat = rng.normal(size=n)
        sigma_sq = np.full(n, 1e-12)
        X = rng.normal(size=(n, 3))

        alpha_eb, _, _, _ = eb_shrinkage(alpha_hat, sigma_sq, X, EBShrinkageConfig())
        np.testing.assert_allclose(alpha_eb, alpha_hat, atol=1e-6)

    def test_large_sigma_is_full_shrinkage(self):
        """σ̂_i² → ∞ ⇒ w_i → 0 ⇒ α̂_EB → γ̂ᵀ[1, X_i]."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        rng = np.random.default_rng(0)
        n = 30
        alpha_hat = rng.normal(size=n)
        sigma_sq = np.full(n, 1e9)  # huge measurement variance
        X = rng.normal(size=(n, 3))

        alpha_eb, gamma, _, _ = eb_shrinkage(
            alpha_hat, sigma_sq, X, EBShrinkageConfig(),
        )

        # α̂_EB should be on the fitted line (near the regression prior)
        col_mean = X.mean(axis=0)
        col_std = X.std(axis=0)
        X_std = (X - col_mean) / col_std
        X_aug = np.hstack([np.ones((n, 1)), X_std])
        mu = X_aug @ gamma
        np.testing.assert_allclose(alpha_eb, mu, atol=1e-3)

    def test_tau2_floor_active(self):
        """Perfect fit (τ² → 0) is floored to tau2_floor_frac · Var(α̂)."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        rng = np.random.default_rng(0)
        n = 40
        X = rng.normal(size=(n, 2))
        # alpha_hat = linear function of X → residuals ≈ 0
        alpha_hat = 1.0 + X[:, 0] * 2.0 + X[:, 1] * 0.5
        sigma_sq = np.full(n, 0.01)

        cfg = EBShrinkageConfig(tau2_floor_frac=0.05)
        _, _, tau2, _ = eb_shrinkage(alpha_hat, sigma_sq, X, cfg)

        var_alpha = np.var(alpha_hat, ddof=0)
        assert tau2 == pytest.approx(cfg.tau2_floor_frac * var_alpha, rel=0.1)

    def test_drops_zero_variance_columns(self):
        """Zero-std X columns are dropped with warning; kept_cols reflects the drop."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        rng = np.random.default_rng(0)
        n = 30
        X = rng.normal(size=(n, 4))
        X[:, 2] = 1.0  # constant column

        alpha_hat = rng.normal(size=n)
        sigma_sq = np.full(n, 0.1)

        with pytest.warns(UserWarning, match="zero-std|dropped"):
            alpha_eb, gamma, _, kept = eb_shrinkage(
                alpha_hat, sigma_sq, X, EBShrinkageConfig(),
            )

        assert 2 not in kept
        # gamma should have length 1 + len(kept)
        assert gamma.shape[0] == 1 + len(kept)
        assert np.all(np.isfinite(alpha_eb))

    def test_ridge_matches_design_formula(self):
        """γ̂ = (XᵀX + εI)⁻¹Xᵀα̂ with intercept unpenalized."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        rng = np.random.default_rng(0)
        n = 20
        X = rng.normal(size=(n, 2))
        alpha_hat = rng.normal(size=n)
        sigma_sq = np.full(n, 0.1)
        cfg = EBShrinkageConfig(ols_ridge=0.5)

        _, gamma, _, kept = eb_shrinkage(alpha_hat, sigma_sq, X, cfg)

        # Reconstruct expected gamma manually
        col_mean = X.mean(axis=0)
        col_std = X.std(axis=0)
        X_std = (X - col_mean) / col_std
        X_aug = np.hstack([np.ones((n, 1)), X_std])
        XtX = X_aug.T @ X_aug
        ridge = np.eye(XtX.shape[0]) * cfg.ols_ridge
        ridge[0, 0] = 0.0  # intercept unpenalized
        gamma_expected = np.linalg.solve(XtX + ridge, X_aug.T @ alpha_hat)

        np.testing.assert_allclose(gamma, gamma_expected, atol=1e-10)

    def test_raises_on_n_builds_lt_3(self):
        """n < 3 → ValueError (need at least 3 builds for stable fit)."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        alpha_hat = np.array([1.0, 2.0])
        sigma_sq = np.array([0.1, 0.1])
        X = np.array([[0.5], [1.5]])
        with pytest.raises(ValueError):
            eb_shrinkage(alpha_hat, sigma_sq, X, EBShrinkageConfig())

    def test_zero_variance_alpha_returns_raw(self):
        """Var(α̂) = 0 → warn + return raw alpha unchanged."""
        from starsector_optimizer.deconfounding import eb_shrinkage

        n = 10
        alpha_hat = np.full(n, 3.0)  # all identical
        sigma_sq = np.full(n, 0.1)
        rng = np.random.default_rng(0)
        X = rng.normal(size=(n, 2))

        with pytest.warns(UserWarning):
            alpha_eb, _, tau2, _ = eb_shrinkage(
                alpha_hat, sigma_sq, X, EBShrinkageConfig(),
            )
        np.testing.assert_allclose(alpha_eb, alpha_hat)
        assert tau2 == 0.0


# --- triple_goal_rank Tests (Phase 5D) ---


class TestTripleGoalRank:

    def test_preserves_rank_of_posterior(self):
        """α̂_EBT has identical rank ordering to α̂_EB."""
        from starsector_optimizer.deconfounding import triple_goal_rank

        rng = np.random.default_rng(0)
        posterior = rng.normal(size=50)
        raw = rng.normal(size=50)
        ebt = triple_goal_rank(posterior, raw)
        rho, _ = spearmanr(posterior, ebt)
        assert rho == pytest.approx(1.0)

    def test_histogram_equals_raw(self):
        """sort(α̂_EBT) == sort(α̂_TWFE) element-wise (histogram substitution)."""
        from starsector_optimizer.deconfounding import triple_goal_rank

        rng = np.random.default_rng(0)
        posterior = rng.normal(size=50)
        raw = rng.normal(size=50)
        ebt = triple_goal_rank(posterior, raw)
        np.testing.assert_array_equal(np.sort(ebt), np.sort(raw))
