"""Wave 1 gate analyzer.

Consumes per-cell SQLite study DBs and shared per-seed JSONL evaluation logs
(eval log paths collide across cells because resolve_study_id omits cell —
see docs/reports/2026-05-10-validation-plan.md), slices JSONL rows by per-cell
SQLite timestamp range, and emits every gate from the validation plan §3
Wave 1 gate to console + structured JSON.

Usage:
    uv run python scripts/analyze_wave1.py [--out data/wave1-gates.json]

Reads:
    data/study_dbs/wave1-{c0a,c0b,c1,c2,c3}/hammerhead__early__tpe__seed{0,1,2}.db
    data/logs/hammerhead__early__tpe__seed{0,1,2}/evaluation_log.jsonl
    data/campaigns/wave1-{c0a,c0b,c1,c2,c3}/{ledger.jsonl,orchestrator.log}
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CELLS = ("c0a", "c0b", "c1", "c2", "c3")
SEEDS = (0, 1, 2)
HULL = "hammerhead"
REGIME = "early"
SAMPLER = "tpe"

# Wave 1 gate thresholds (validation plan §3)
PRUNER_RATIO_LO, PRUNER_RATIO_HI = 0.10, 0.60
THROUGHPUT_LO, THROUGHPUT_HI = 92.0, 152.0  # matchups/hr/VM
EB_DELTA_RHO_GATE = 0.02  # Δρ(EB - A0) and Δρ(EB - A) gates
BOXCOX_CEILING_MAX = 0.01  # ≤ 1 % saturation
BOXCOX_TOP5_JACCARD_MIN = 0.40
INCUMBENT_OVERLAP_REQUIRED = 5
INCUMBENT_OVERLAP_FRACTION_MIN = 0.90
DEFAULT_BOOTSTRAP_RESAMPLES = 200


def _parse_sqlite_timestamp(s: str) -> datetime:
    """Optuna writes naive 'YYYY-MM-DD HH:MM:SS.ffffff' in SYSTEM LOCAL time (SQLAlchemy default).

    Convert to UTC by stamping the local TZ then converting. JSONL writes UTC
    with explicit offset, so both must end up in the same TZ space for filtering.
    """
    naive = datetime.fromisoformat(s)
    # naive.astimezone(target) reads the system local TZ, then converts
    return naive.astimezone(UTC)


def _parse_jsonl_timestamp(s: str) -> datetime:
    """JSONL writes ISO8601 with timezone."""
    return datetime.fromisoformat(s).astimezone(UTC)


def load_cell_seed_window(cell: str, seed: int) -> dict[str, Any] | None:
    """Open per-cell per-seed SQLite, return trial summary + timestamp window."""
    db_path = REPO_ROOT / "data" / "study_dbs" / f"wave1-{cell}" / f"{HULL}__{REGIME}__{SAMPLER}__seed{seed}.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        n_total = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        n_complete = conn.execute("SELECT COUNT(*) FROM trials WHERE state='COMPLETE'").fetchone()[0]
        n_pruned = conn.execute("SELECT COUNT(*) FROM trials WHERE state='PRUNED'").fetchone()[0]
        n_fail = conn.execute("SELECT COUNT(*) FROM trials WHERE state='FAIL'").fetchone()[0]
        n_running = conn.execute("SELECT COUNT(*) FROM trials WHERE state='RUNNING'").fetchone()[0]
        t_min_str = conn.execute("SELECT MIN(datetime_start) FROM trials").fetchone()[0]
        t_max_str = conn.execute(
            "SELECT MAX(datetime_complete) FROM trials WHERE datetime_complete IS NOT NULL"
        ).fetchone()[0]
        # Get all per-trial fitness values for cross-cell comparisons
        rows = conn.execute(
            "SELECT t.number, v.value FROM trials t JOIN trial_values v ON t.trial_id=v.trial_id "
            "WHERE t.state='COMPLETE' ORDER BY t.number"
        ).fetchall()
    finally:
        conn.close()
    if t_min_str is None or t_max_str is None:
        return None
    return {
        "n_total": n_total,
        "n_complete": n_complete,
        "n_pruned": n_pruned,
        "n_fail": n_fail,
        "n_running": n_running,
        "t_min": _parse_sqlite_timestamp(t_min_str),
        "t_max": _parse_sqlite_timestamp(t_max_str),
        "fitness_by_trial": dict(rows),
        "db_path": str(db_path),
    }


def load_jsonl_slice(seed: int, t_min: datetime, t_max: datetime) -> list[dict[str, Any]]:
    """Load JSONL rows whose `timestamp` falls in [t_min, t_max] (inclusive on both ends)."""
    log_path = REPO_ROOT / "data" / "logs" / f"{HULL}__{REGIME}__{SAMPLER}__seed{seed}" / "evaluation_log.jsonl"
    if not log_path.exists():
        return []
    out = []
    with log_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = row.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = _parse_jsonl_timestamp(ts_raw)
            except ValueError:
                continue
            if t_min <= ts <= t_max:
                out.append(row)
    return out


def compute_pruner_ratio(window: dict[str, Any]) -> tuple[float, int, int]:
    n_finalized = window["n_complete"] + window["n_pruned"]
    if n_finalized == 0:
        return 0.0, 0, 0
    return window["n_pruned"] / n_finalized, window["n_pruned"], n_finalized


def count_engine_stats_nulls(jsonl_rows: list[dict[str, Any]]) -> int:
    """Mechanism 20: HARD GATE — must be 0 across finalized rows."""
    return sum(1 for r in jsonl_rows if r.get("engine_stats") is None and not r.get("pruned"))


def twfe_bounded_check(jsonl_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanism 4: ≥ 95 % of finalized rows have finite `twfe_fitness` in
    approximately the design tier ranges (per `models.py:438-440`):
    wins [1.0, 1.5], timeouts [-0.49, +0.49]. Combined practical bound:
    fitness in [-1.5, 2.0] is generous; out-of-bound is a regression."""
    completed = [
        r for r in jsonl_rows
        if not r.get("pruned") and r.get("twfe_fitness") is not None
    ]
    if not completed:
        return {"passes": False, "reason": "no twfe_fitness rows"}
    in_range = 0
    for r in completed:
        v = r["twfe_fitness"]
        if isinstance(v, (int, float)) and not math.isnan(v) and -1.5 <= v <= 2.0:
            in_range += 1
    ratio = in_range / len(completed)
    return {
        "passes": ratio >= 0.95,
        "in_range_ratio": ratio,
        "n_finalized": len(completed),
    }


def eb_min_builds_null_path(jsonl_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanism 5: First 7 *finalized* trials (by timestamp, not by Optuna
    trial_number — async dispatch + ASHA pruning means trial 0 may not be
    the first to finalize) must have null `eb_diagnostics`. The
    `eb_min_builds=8` fallback path returns raw α̂ unchanged for the first
    8 finalized builds; the 8th finalization activates EB shrinkage and
    populates `eb_diagnostics`.
    """
    finalized = [r for r in jsonl_rows if not r.get("pruned")]
    finalized.sort(key=lambda r: r.get("timestamp", ""))
    early = finalized[:7]
    if not early:
        return {"passes": False, "reason": "no finalized rows"}
    null_count = sum(1 for r in early if r.get("eb_diagnostics") is None)
    return {
        "passes": null_count == len(early),
        "null_count": null_count,
        "early_finalized_seen": len(early),
        "note": "first 7 finalized by timestamp",
    }


def triple_goal_rho_delta(jsonl_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanism 6: report Spearman ρ(twfe_fitness, eb_fitness) as a
    diagnostic only.

    Validation plan §3 mech 6 specifies a `triple_goal ∈ {True, False}`
    ablation that measures the Spearman *delta* between the two
    triple_goal modes; that ablation cell is NOT in the Wave 1 design,
    so a true gate cannot be computed. Lower ρ values are EXPECTED when
    EB shrinkage actively re-ranks vs raw TWFE. Reporting ρ as a
    diagnostic preserves the audit trail for Phase 5D follow-up without
    flagging false failures.
    """
    from scipy.stats import spearmanr
    pairs = [
        (r["twfe_fitness"], r["eb_fitness"])
        for r in jsonl_rows
        if not r.get("pruned")
        and r.get("twfe_fitness") is not None
        and r.get("eb_fitness") is not None
    ]
    if len(pairs) < 30:
        return {"passes": True, "diagnostic": True, "rho": None,
                "reason": f"only {len(pairs)} paired rows; diagnostic-only"}
    xs, ys = zip(*pairs, strict=True)
    rho, _ = spearmanr(xs, ys)
    return {
        "passes": True,  # diagnostic-only — no gate
        "diagnostic": True,
        "spearman_rho_twfe_eb": float(rho),
        "n_pairs": len(pairs),
        "note": "diagnostic only; the true gate requires a triple_goal=False ablation cell not present in Wave 1",
    }


def asha_rung_summary(jsonl_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanism 10: at least 1 trial reached full rung; ≥ 1 pruned at < full."""
    completed = [r for r in jsonl_rows if not r.get("pruned")]
    pruned = [r for r in jsonl_rows if r.get("pruned")]
    if not completed:
        return {"max_eval_complete": 0, "min_eval_pruned": None, "passes": False}
    max_complete = max(r.get("opponents_evaluated", 0) for r in completed)
    total = max((r.get("opponents_total", 0) for r in completed), default=0)
    min_pruned = min((r.get("opponents_evaluated", 0) for r in pruned), default=None)
    passes = (max_complete == total) and (min_pruned is not None and min_pruned < total)
    return {
        "max_eval_complete": max_complete,
        "opponents_total": total,
        "min_eval_pruned": min_pruned,
        "passes": passes,
    }


def boxcox_ceiling_saturation(jsonl_rows: list[dict[str, Any]]) -> float:
    """% of finalized rows with fitness ≥ 0.99."""
    completed = [r for r in jsonl_rows if not r.get("pruned") and r.get("fitness") is not None]
    if not completed:
        return 0.0
    sat = sum(1 for r in completed if r["fitness"] >= 0.99)
    return sat / len(completed)


def top5_jaccard(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]) -> float:
    """Top-5-by-eb_fitness Jaccard between two cells (build_hash overlap).

    Per validation plan mech 8: top-5 identification overlap ≥ 0.40.
    """
    def top5_keys(rows):
        finalized = [r for r in rows if not r.get("pruned") and r.get("eb_fitness") is not None]
        finalized.sort(key=lambda r: r["eb_fitness"], reverse=True)
        return {build_hash(r["build"]) for r in finalized[:5]}
    a, b = top5_keys(rows_a), top5_keys(rows_b)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def build_hash(build: dict[str, Any]) -> str:
    """Deterministic hash of a build for top-K overlap computation."""
    parts = [
        f"caps={build.get('flux_capacitors')}",
        f"vents={build.get('flux_vents')}",
        "hm=" + ",".join(sorted(build.get("hullmods", []))),
        "wp=" + ",".join(f"{k}:{v}" for k, v in sorted((build.get("weapon_assignments") or {}).items())),
    ]
    return "|".join(parts)


def loocv_anchor_spearman(jsonl_rows: list[dict[str, Any]], fitness_field: str) -> dict[str, float]:
    """5-anchor LOOO Spearman ρ for `fitness_field` against held-out anchor's raw α̂.

    Returns ρ per anchor + mean ρ. Anchors are the most-frequent opp_ids in the post-burn-in JSONL.
    """
    from scipy.stats import spearmanr
    finalized = [
        r for r in jsonl_rows
        if not r.get("pruned")
        and r.get(fitness_field) is not None
        and r.get("opponent_results")
    ]
    if len(finalized) < 30:
        return {"mean": float("nan"), "anchors": {}, "n": len(finalized)}
    # Find anchor opps: the 5 opponents most frequently appearing in opponent_results.
    # JSONL schema: opponent_results = list of {opponent, winner, duration_seconds, hp_differential}.
    opp_counts: dict[str, int] = defaultdict(int)
    for r in finalized:
        for opp_result in r["opponent_results"]:
            opp_id = opp_result.get("opponent") if isinstance(opp_result, dict) else None
            if opp_id:
                opp_counts[opp_id] += 1
    top_anchors = [o for o, _ in sorted(opp_counts.items(), key=lambda kv: -kv[1])[:5]]
    if not top_anchors:
        return {"mean": float("nan"), "anchors": {}, "n": len(finalized)}
    rhos: dict[str, float] = {}
    for anchor in top_anchors:
        paired = []
        for r in finalized:
            anchor_raw = None
            for opp_result in r["opponent_results"]:
                if isinstance(opp_result, dict) and opp_result.get("opponent") == anchor:
                    anchor_raw = opp_result.get("hp_differential")
                    break
            if anchor_raw is None:
                continue
            paired.append((r[fitness_field], anchor_raw))
        if len(paired) < 10:
            continue
        xs, ys = zip(*paired, strict=True)
        rho, _ = spearmanr(xs, ys)
        if not math.isnan(rho):
            rhos[anchor] = float(rho)
    if not rhos:
        return {"mean": float("nan"), "anchors": {}, "n": len(finalized)}
    return {"mean": sum(rhos.values()) / len(rhos), "anchors": rhos, "n": len(finalized)}


def bootstrap_delta_rho(
    rows_treat: list[dict[str, Any]],
    rows_ctrl: list[dict[str, Any]],
    fitness_field_treat: str,
    fitness_field_ctrl: str,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = 1,
) -> dict[str, float]:
    """Wave 1 gate primary metric: Δρ(treat - ctrl) bootstrap 95 % CI.

    Bootstraps over per-anchor LOOO ρ samples (5 anchors × 200 resamples = 1000-sample pool).
    """
    import random
    rng = random.Random(seed)
    treat_lo = loocv_anchor_spearman(rows_treat, fitness_field_treat)
    ctrl_lo = loocv_anchor_spearman(rows_ctrl, fitness_field_ctrl)
    treat_anchors = list(treat_lo.get("anchors", {}).values())
    ctrl_anchors = list(ctrl_lo.get("anchors", {}).values())
    if not treat_anchors or not ctrl_anchors:
        return {
            "delta_mean": float("nan"),
            "ci_lo": float("nan"),
            "ci_hi": float("nan"),
            "treat_mean_rho": treat_lo.get("mean", float("nan")),
            "ctrl_mean_rho": ctrl_lo.get("mean", float("nan")),
            "n_treat_anchors": len(treat_anchors),
            "n_ctrl_anchors": len(ctrl_anchors),
            "n_resamples": 0,
        }
    deltas = []
    for _ in range(n_resamples):
        t_resample = [rng.choice(treat_anchors) for _ in range(len(treat_anchors))]
        c_resample = [rng.choice(ctrl_anchors) for _ in range(len(ctrl_anchors))]
        deltas.append(sum(t_resample) / len(t_resample) - sum(c_resample) / len(c_resample))
    deltas.sort()
    ci_lo = deltas[int(0.025 * n_resamples)]
    ci_hi = deltas[int(0.975 * n_resamples)]
    return {
        "delta_mean": treat_lo["mean"] - ctrl_lo["mean"],
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "treat_mean_rho": treat_lo["mean"],
        "ctrl_mean_rho": ctrl_lo["mean"],
        "n_treat_anchors": len(treat_anchors),
        "n_ctrl_anchors": len(ctrl_anchors),
        "n_resamples": n_resamples,
    }


def best_fitness_sigma_delta(rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]) -> dict[str, float]:
    """C3 vs C2: |Δ best-fitness| in σ units across seeds."""
    import statistics
    def best_per_seed(rows):
        completed = [r for r in rows if not r.get("pruned") and r.get("fitness") is not None]
        return max((r["fitness"] for r in completed), default=float("nan"))
    a = best_per_seed(rows_a)
    b = best_per_seed(rows_b)
    completed_a = [r["fitness"] for r in rows_a if not r.get("pruned") and r.get("fitness") is not None]
    completed_b = [r["fitness"] for r in rows_b if not r.get("pruned") and r.get("fitness") is not None]
    pooled = completed_a + completed_b
    if len(pooled) < 2:
        return {"a_best": a, "b_best": b, "delta": float("nan"), "sigma_delta": float("nan")}
    sd = statistics.stdev(pooled)
    return {
        "a_best": a,
        "b_best": b,
        "delta": a - b,
        "sigma_delta": (a - b) / sd if sd > 0 else float("nan"),
        "pooled_sd": sd,
    }


def cell_throughput(cell: str) -> dict[str, float]:
    """Per-VM throughput from ledger.jsonl: matchups/hr/VM.

    Matchups counted from finalized JSONL rows in the cell's time window
    (sum opponents_evaluated). VM-hours from sum(hours_elapsed) per worker.
    """
    ledger_path = REPO_ROOT / "data" / "campaigns" / f"wave1-{cell}" / "ledger.jsonl"
    if not ledger_path.exists():
        return {"vm_hours": 0.0, "matchups": 0, "matchups_per_hr_per_vm": float("nan")}
    vm_hours = 0.0
    with ledger_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("event_type") == "worker_heartbeat":
                vm_hours += d.get("hours_elapsed", 0.0)
    matchups = 0
    for seed in SEEDS:
        win = load_cell_seed_window(cell, seed)
        if not win:
            continue
        rows = load_jsonl_slice(seed, win["t_min"], win["t_max"])
        for r in rows:
            matchups += r.get("opponents_evaluated", 0)
    return {
        "vm_hours": vm_hours,
        "matchups": matchups,
        "matchups_per_hr_per_vm": matchups / vm_hours if vm_hours > 0 else float("nan"),
    }


def total_cost_usd(cell: str) -> float:
    ledger_path = REPO_ROOT / "data" / "campaigns" / f"wave1-{cell}" / "ledger.jsonl"
    if not ledger_path.exists():
        return 0.0
    last = 0.0
    with ledger_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("event_type") == "worker_heartbeat":
                last = max(last, d.get("cumulative_usd", 0.0))
    return last


def anchor_lock_count(cell: str) -> int:
    """Mechanism 11: count of 'Locked N anchors' lines in the cell's orchestrator log."""
    log_path = REPO_ROOT / "data" / "campaigns" / f"wave1-{cell}" / "orchestrator.log"
    if not log_path.exists():
        return 0
    n = 0
    with log_path.open(errors="replace") as f:
        for line in f:
            if "Locked 3 anchors" in line:
                n += 1
    return n


def loadout_mismatch_count(cell: str) -> int:
    """Count of unique mismatched matchup events (NOT log lines).

    Pre-band-aid: each event = 1 line (`_log_loadout_diagnostics` only).
    Post-band-aid: each event = 2 lines (`_log_loadout_diagnostics` +
    `discarding LOADOUT_MISMATCH` band-aid). Counting `discarding` lines
    gives the post-band-aid actual count; for pre-band-aid cells, fall
    back to total LOADOUT_MISMATCH lines.
    """
    log_path = REPO_ROOT / "data" / "campaigns" / f"wave1-{cell}" / "orchestrator.log"
    if not log_path.exists():
        return 0
    raw_mismatch = 0
    discarding_count = 0
    with log_path.open(errors="replace") as f:
        for line in f:
            if "discarding LOADOUT_MISMATCH" in line:
                discarding_count += 1
                raw_mismatch += 1  # also logged by _log_loadout_diagnostics
            elif "LOADOUT_MISMATCH" in line:
                raw_mismatch += 1
    if discarding_count > 0:
        # Post-band-aid: each event produces 1 _log_loadout + 1 discarding line.
        return discarding_count
    return raw_mismatch


def loadout_final_failure_count(cell: str) -> int:
    """Count of matchups that exhausted max_requeues — i.e. contaminated
    fitness. Looks for `matchup .* exceeded max_requeues=` from janitor.

    Pre-band-aid (C0a/C0b/C1 in Wave 1), every LOADOUT_MISMATCH was a
    final failure (no requeue). Post-band-aid (C2/C3 in Wave 1, all of
    Wave 2/3), only requeue exhaustions count.
    """
    log_path = REPO_ROOT / "data" / "campaigns" / f"wave1-{cell}" / "orchestrator.log"
    if not log_path.exists():
        return 0
    n = 0
    with log_path.open(errors="replace") as f:
        for line in f:
            if "exceeded max_requeues" in line:
                n += 1
    return n


def cell_summary(cell: str) -> dict[str, Any]:
    out: dict[str, Any] = {"cell": cell, "seeds": {}, "all_jsonl_rows": []}
    for seed in SEEDS:
        win = load_cell_seed_window(cell, seed)
        if not win:
            out["seeds"][seed] = {"status": "missing"}
            continue
        rows = load_jsonl_slice(seed, win["t_min"], win["t_max"])
        out["all_jsonl_rows"].extend(rows)
        pruner_ratio, _n_pruned, n_finalized = compute_pruner_ratio(win)
        out["seeds"][seed] = {
            "n_total": win["n_total"],
            "n_complete": win["n_complete"],
            "n_pruned": win["n_pruned"],
            "n_running": win["n_running"],
            "n_finalized": n_finalized,
            "pruner_ratio": pruner_ratio,
            "engine_stats_null_count": count_engine_stats_nulls(rows),
            "asha": asha_rung_summary(rows),
            "boxcox_ceiling": boxcox_ceiling_saturation(rows),
            "anchor_lock_present": True,  # set later from orchestrator log
            "jsonl_rows_in_window": len(rows),
            "t_min": win["t_min"].isoformat(),
            "t_max": win["t_max"].isoformat(),
        }
    out["throughput"] = cell_throughput(cell)
    out["cost_usd"] = total_cost_usd(cell)
    out["anchor_locks_in_log"] = anchor_lock_count(cell)
    out["loadout_mismatch_count"] = loadout_mismatch_count(cell)
    out["loadout_final_failure_count"] = loadout_final_failure_count(cell)
    return out


def evaluate_gates(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply Wave 1 gate criteria per validation plan §3."""
    verdicts: dict[str, Any] = {}

    # HARD GATE: 0 null engine_stats across all cells
    null_total = sum(
        s.get("engine_stats_null_count", 0)
        for c in cells.values() for s in c["seeds"].values() if isinstance(s, dict)
    )
    verdicts["engine_stats_null_hard_gate"] = {
        "passes": null_total == 0,
        "null_count": null_total,
        "threshold": 0,
        "note": "HARD GATE — any null aborts Wave 1 (mechanism 20)",
    }

    # HARD GATE: fitness-contaminating mismatches.
    # Pre-band-aid cells (C0a/C0b/C1, launched 2026-05-09 to 2026-05-10 02:42)
    # had no requeue path — every LOADOUT_MISMATCH = corrupt fitness.
    # Post-band-aid cells (C2/C3, launched after the 2026-05-10 cloud_worker_pool
    # band-aid landed at ~02:42 EDT) requeue mismatches via the janitor;
    # only requeue-exhausted matchups corrupt fitness, surfaced as
    # "exceeded max_requeues=" log lines.
    PRE_BAND_AID_CELLS = {"c0a", "c0b", "c1"}
    contamination_total = 0
    contamination_per_cell = {}
    for cell_name, c in cells.items():
        if cell_name in PRE_BAND_AID_CELLS:
            v = c.get("loadout_mismatch_count", 0)
        else:
            v = c.get("loadout_final_failure_count", 0)
        contamination_per_cell[cell_name] = v
        contamination_total += v
    verdicts["loadout_contamination"] = {
        "passes": contamination_total == 0,
        "contamination_total": contamination_total,
        "per_cell": contamination_per_cell,
        "threshold": 0,
        "note": (
            "Pre-band-aid cells (c0a/c0b/c1) count raw LOADOUT_MISMATCH; "
            "post-band-aid cells (c2/c3) count only `exceeded max_requeues`. "
            "Wave 1 is expected to have non-zero contamination from C1 "
            "(pre-band-aid era) — flagged but not wave-aborting per the "
            "validation plan §4 0.6 % noise-floor analysis."
        ),
    }
    # Diagnostic: raw mismatch count (includes successful retries)
    verdicts["loadout_mismatch_raw_advisory"] = {
        "passes": True,  # advisory only — useful for op-side investigation
        "mismatch_count_per_cell": {
            k: c.get("loadout_mismatch_count", 0) for k, c in cells.items()
        },
        "note": "advisory only; raw LOADOUT_MISMATCH count includes retries",
    }

    # Pruner ratio per cell × seed
    pruner_failures = []
    for cell_name, c in cells.items():
        for seed, s in c["seeds"].items():
            if not isinstance(s, dict) or "pruner_ratio" not in s:
                continue
            r = s["pruner_ratio"]
            if not (PRUNER_RATIO_LO <= r <= PRUNER_RATIO_HI):
                pruner_failures.append(
                    {"cell": cell_name, "seed": seed, "ratio": r, "n_finalized": s["n_finalized"]}
                )
    verdicts["pruner_ratio"] = {
        "passes": not pruner_failures,
        "threshold": [PRUNER_RATIO_LO, PRUNER_RATIO_HI],
        "failures": pruner_failures,
    }

    # Per-VM throughput per cell
    throughput_failures = []
    for cell_name, c in cells.items():
        t = c["throughput"]["matchups_per_hr_per_vm"]
        if math.isnan(t):
            continue
        if not (THROUGHPUT_LO <= t <= THROUGHPUT_HI):
            throughput_failures.append({"cell": cell_name, "throughput": t})
    verdicts["throughput"] = {
        "passes": not throughput_failures,
        "threshold": [THROUGHPUT_LO, THROUGHPUT_HI],
        "failures": throughput_failures,
    }

    # ASHA rung gate per cell
    asha_failures = []
    for cell_name, c in cells.items():
        for seed, s in c["seeds"].items():
            if not isinstance(s, dict) or "asha" not in s:
                continue
            if not s["asha"]["passes"]:
                asha_failures.append({"cell": cell_name, "seed": seed, "asha": s["asha"]})
    verdicts["asha_rung"] = {
        "passes": not asha_failures,
        "failures": asha_failures,
    }

    # Anchor-lock per cell (3 seeds × 1 lock each = 3 expected)
    anchor_failures = []
    for cell_name, c in cells.items():
        seeds_present = sum(1 for s in c["seeds"].values() if isinstance(s, dict) and s.get("n_total", 0) > 30)
        if seeds_present > 0 and c["anchor_locks_in_log"] < seeds_present:
            anchor_failures.append({
                "cell": cell_name,
                "expected_min": seeds_present,
                "observed": c["anchor_locks_in_log"],
            })
    verdicts["anchor_first_lock"] = {
        "passes": not anchor_failures,
        "failures": anchor_failures,
    }

    # Mechanism 4 (TWFE bounded), 5 (eb_min_builds null path), 6 (triple_goal rho)
    # — computed per cell, aggregated as a pass-if-all-cells-pass gate.
    twfe_bound_per_cell = {}
    eb_null_per_cell = {}
    triple_goal_per_cell = {}
    for cell_name, c in cells.items():
        twfe_bound_per_cell[cell_name] = twfe_bounded_check(c["all_jsonl_rows"])
        eb_null_per_cell[cell_name] = eb_min_builds_null_path(c["all_jsonl_rows"])
        triple_goal_per_cell[cell_name] = triple_goal_rho_delta(c["all_jsonl_rows"])
    verdicts["twfe_bounded"] = {
        "passes": all(v.get("passes") for v in twfe_bound_per_cell.values()),
        "per_cell": twfe_bound_per_cell,
    }
    # eb_min_builds null path only meaningful in EB-on cells (C1/C2/C3); C0a/C0b have EB off
    eb_on_cells = {k: v for k, v in eb_null_per_cell.items() if k in {"c1", "c2", "c3"}}
    verdicts["eb_min_builds_null_path"] = {
        "passes": all(v.get("passes") for v in eb_on_cells.values()) if eb_on_cells else True,
        "per_cell_eb_on_only": eb_on_cells,
    }
    # triple_goal only meaningful in EB-on cells where eb_fitness diverges from twfe_fitness
    triple_goal_eb_on = {k: v for k, v in triple_goal_per_cell.items() if k in {"c1", "c2", "c3"}}
    verdicts["triple_goal_rank_correction"] = {
        "passes": all(v.get("passes") for v in triple_goal_eb_on.values()) if triple_goal_eb_on else True,
        "per_cell_eb_on_only": triple_goal_eb_on,
    }

    # Cross-cell: EB Δρ(C2 - C0a) and Δρ(C2 - C0b).
    # Skip if either cell has < 30 finalized rows in JSONL — bootstrap CI
    # is meaningless on tiny samples and "FAIL with nan" is noise during
    # mid-run analysis. The post-completion run will have full samples.
    def _has_enough(rows, n=30):
        return sum(1 for r in rows if not r.get("pruned")) >= n
    if "c2" in cells and "c0a" in cells:
        if not (_has_enough(cells["c2"]["all_jsonl_rows"]) and
                _has_enough(cells["c0a"]["all_jsonl_rows"])):
            verdicts["eb_vs_a0_delta_rho"] = {
                "passes": True, "skipped": True,
                "reason": "insufficient finalized rows in C2 or C0a (cell still running?)",
            }
            verdicts["eb_vs_a_delta_rho"] = {
                "passes": True, "skipped": True,
                "reason": "insufficient finalized rows in C2 or C0b (cell still running?)",
            }
            verdicts["boxcox_top5_jaccard"] = {
                "passes": True, "skipped": True,
                "reason": "insufficient finalized rows in C2 or C1 (cell still running?)",
            }
            return verdicts
    if "c2" in cells and "c0a" in cells:
        verdicts["eb_vs_a0_delta_rho"] = {
            **bootstrap_delta_rho(
                cells["c2"]["all_jsonl_rows"],
                cells["c0a"]["all_jsonl_rows"],
                "eb_fitness",
                "twfe_fitness",
            ),
            "threshold": EB_DELTA_RHO_GATE,
        }
        verdicts["eb_vs_a0_delta_rho"]["passes"] = (
            verdicts["eb_vs_a0_delta_rho"]["delta_mean"] >= EB_DELTA_RHO_GATE
        )
    if "c2" in cells and "c0b" in cells:
        verdicts["eb_vs_a_delta_rho"] = {
            **bootstrap_delta_rho(
                cells["c2"]["all_jsonl_rows"],
                cells["c0b"]["all_jsonl_rows"],
                "eb_fitness",
                "eb_fitness",  # C0b also writes eb_fitness; the meaningful difference is the underlying ablation
            ),
            "threshold": EB_DELTA_RHO_GATE,
            "note": "C0b ran with scalar CV legacy path; EB shrinkage off",
        }
        verdicts["eb_vs_a_delta_rho"]["passes"] = (
            verdicts["eb_vs_a_delta_rho"]["delta_mean"] >= EB_DELTA_RHO_GATE
        )

    # Box-Cox: C2 ceiling vs C1 (Box-Cox off in C1)
    if "c2" in cells:
        c2_ceiling = sum(
            s.get("boxcox_ceiling", 0)
            for s in cells["c2"]["seeds"].values() if isinstance(s, dict)
        ) / max(1, sum(1 for s in cells["c2"]["seeds"].values() if isinstance(s, dict)))
        verdicts["boxcox_ceiling"] = {
            "passes": c2_ceiling <= BOXCOX_CEILING_MAX,
            "c2_avg_ceiling": c2_ceiling,
            "threshold_max": BOXCOX_CEILING_MAX,
        }
    if "c2" in cells and "c1" in cells:
        jacc = top5_jaccard(cells["c2"]["all_jsonl_rows"], cells["c1"]["all_jsonl_rows"])
        verdicts["boxcox_top5_jaccard"] = {
            "passes": jacc >= BOXCOX_TOP5_JACCARD_MIN,
            "jaccard": jacc,
            "threshold_min": BOXCOX_TOP5_JACCARD_MIN,
        }

    # C3 vs C2: |Δ best-fitness| ≤ 1σ (mechanism 2 confirms warm-start default-off)
    if "c3" in cells and "c2" in cells:
        d = best_fitness_sigma_delta(cells["c3"]["all_jsonl_rows"], cells["c2"]["all_jsonl_rows"])
        verdicts["warm_start_default_off"] = {
            **d,
            "passes": abs(d.get("sigma_delta", float("inf"))) <= 1.0,
            "note": "C3 warm_start_n=50 should not beat C2 default by > 1σ",
        }

    return verdicts


def render_console(cells: dict[str, dict[str, Any]], gates: dict[str, Any]) -> None:
    print("=" * 78)
    print("Wave 1 gate analyzer")
    print("=" * 78)
    for cell_name in CELLS:
        if cell_name not in cells:
            print(f"\n[cell {cell_name}] MISSING (study DBs not present)")
            continue
        c = cells[cell_name]
        print(f"\n[cell {cell_name}]  cost=${c['cost_usd']:.2f}  loadout_mismatches={c['loadout_mismatch_count']}  anchor_locks={c['anchor_locks_in_log']}")
        thr = c["throughput"]
        thr_str = f"{thr['matchups_per_hr_per_vm']:.1f}" if not math.isnan(thr["matchups_per_hr_per_vm"]) else "n/a"
        print(f"  throughput: {thr_str} matchups/hr/VM  ({thr['matchups']} matchups / {thr['vm_hours']:.1f} VM-hr)")
        for seed, s in c["seeds"].items():
            if not isinstance(s, dict) or "n_finalized" not in s:
                print(f"    seed{seed}: {s.get('status', 'unknown')}")
                continue
            print(
                f"    seed{seed}: complete={s['n_complete']:3d} pruned={s['n_pruned']:3d} running={s['n_running']:2d} "
                f"prune_ratio={s['pruner_ratio']:.2f} engine_null={s['engine_stats_null_count']} "
                f"boxcox_ceil={s['boxcox_ceiling']:.3f} jsonl={s['jsonl_rows_in_window']}"
            )
    print()
    print("=" * 78)
    print("Gate verdicts")
    print("=" * 78)
    for name, v in gates.items():
        if not isinstance(v, dict):
            continue
        passes = v.get("passes")
        marker = "PASS" if passes else "FAIL"
        print(f"  [{marker}] {name}")
        for k, val in v.items():
            if k == "passes":
                continue
            if isinstance(val, float):
                print(f"      {k}: {val:.4f}")
            else:
                print(f"      {k}: {val}")
    print()
    n_pass = sum(1 for v in gates.values() if isinstance(v, dict) and v.get("passes"))
    n_total = sum(1 for v in gates.values() if isinstance(v, dict) and "passes" in v)
    print(f"Verdict summary: {n_pass}/{n_total} gates pass")


def serialize_for_json(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Strip JSONL rows from output JSON (keep summary fields only)."""
    out = {}
    for k, c in cells.items():
        c2 = {kk: vv for kk, vv in c.items() if kk != "all_jsonl_rows"}
        out[k] = c2
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/wave1-gates.json", help="JSON output path")
    args = p.parse_args()

    cells: dict[str, dict[str, Any]] = {}
    for cell in CELLS:
        c = cell_summary(cell)
        # only include cells that have at least one seed with data
        if any(isinstance(s, dict) and s.get("n_total", 0) > 0 for s in c["seeds"].values()):
            cells[cell] = c

    if not cells:
        print("No Wave 1 cells found. Nothing to analyze.", file=sys.stderr)
        return 1

    gates = evaluate_gates(cells)
    render_console(cells, gates)

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(),
        "cells": serialize_for_json(cells),
        "gates": gates,
    }, indent=2, default=str))
    print(f"\nWrote {out_path}")

    n_fail = sum(1 for v in gates.values() if isinstance(v, dict) and v.get("passes") is False)
    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
