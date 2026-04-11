"""Parameter importance analysis via fANOVA.

Wraps Optuna's built-in parameter importance computation to identify
which search space dimensions most influence fitness.

See spec 26 for design rationale.
"""

from __future__ import annotations

import logging

import optuna
from optuna.trial import TrialState

from .models import ImportanceResult

logger = logging.getLogger(__name__)


def analyze_importance(
    study: optuna.Study,
    min_trials: int = 20,
) -> ImportanceResult:
    """Run fANOVA importance analysis on a completed study.

    Args:
        study: Optuna study with completed trials.
        min_trials: Minimum completed trials required. Raises ValueError if fewer.

    Returns:
        ImportanceResult with per-parameter importance scores.
    """
    completed = [t for t in study.trials if t.state == TrialState.COMPLETE]
    if len(completed) < min_trials:
        raise ValueError(
            f"Need >= {min_trials} completed trials, got {len(completed)}"
        )
    raw = optuna.importance.get_param_importances(study)
    return ImportanceResult(importances=raw)


def print_importance_report(
    result: ImportanceResult,
    top_n: int = 20,
) -> str:
    """Format importance results as a readable table string."""
    sorted_params = sorted(
        result.importances.items(), key=lambda x: x[1], reverse=True
    )[:top_n]
    col_name = 40
    col_val = 10
    lines = [f"{'Parameter':<{col_name}} {'Importance':>{col_val}}"]
    lines.append("-" * (col_name + col_val + 1))
    for name, imp in sorted_params:
        lines.append(f"{name:<{col_name}} {imp:>{col_val}.4f}")
    return "\n".join(lines)
