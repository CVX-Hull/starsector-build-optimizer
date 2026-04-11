"""Tests for parameter importance analysis via fANOVA."""

import optuna
import pytest

from starsector_optimizer.importance import analyze_importance, print_importance_report
from starsector_optimizer.models import ImportanceResult


def _make_study_with_trials(n_trials: int) -> optuna.Study:
    """Create a study with n completed trials and random params."""
    import random

    study = optuna.create_study(direction="maximize")
    for i in range(n_trials):
        trial = study.ask({
            "x": optuna.distributions.FloatDistribution(0.0, 1.0),
            "y": optuna.distributions.FloatDistribution(0.0, 1.0),
            "cat": optuna.distributions.CategoricalDistribution(["a", "b", "c"]),
        })
        # Score based on x (so x should be most important)
        study.tell(trial, trial.params["x"] * 0.8 + random.random() * 0.2)
    return study


class TestAnalyzeImportance:

    def test_analyze_importance_returns_result(self):
        """Returns an ImportanceResult with float importance values."""
        study = _make_study_with_trials(30)
        result = analyze_importance(study)
        assert isinstance(result, ImportanceResult)
        assert all(isinstance(v, float) for v in result.importances.values())
        assert len(result.importances) > 0

    def test_importances_sum_to_approximately_one(self):
        """Importance values sum to approximately 1.0."""
        study = _make_study_with_trials(30)
        result = analyze_importance(study)
        total = sum(result.importances.values())
        assert abs(total - 1.0) < 0.05

    def test_too_few_trials_raises(self):
        """Study with too few completed trials raises ValueError."""
        study = _make_study_with_trials(5)
        with pytest.raises(ValueError, match="Need >= 20"):
            analyze_importance(study, min_trials=20)

    def test_print_importance_report(self):
        """Report contains parameter names and formatted floats."""
        study = _make_study_with_trials(30)
        result = analyze_importance(study)
        report = print_importance_report(result, top_n=3)
        assert "Parameter" in report
        assert "Importance" in report
        # Should contain at least one param name
        assert any(name in report for name in result.importances)
