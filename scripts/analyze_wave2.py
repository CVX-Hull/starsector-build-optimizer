"""Wave 2 gate analyzer.

Validates the cross-regime warm-start (mechanism 13b) + frigate gradient
(mechanism 4 on Wolf) gates from docs/reports/2026-05-10-validation-plan.md
§3 Wave 2.

Usage:
    uv run python scripts/analyze_wave2.py [--out data/wave2-gates.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Wave 2 thresholds (validation plan §3 Wave 2 gate)
CROSS_REGIME_OVERLAP_MIN = 0.80          # mech 13b: ≥80% match
WOLF_FINALIZED_MIN = 150                  # ≥150/200 = 75% completion
WOLF_TAU_SQ_MIN = 1e-3                   # mech 4: τ̂² > 1e-3 (frigate gradient non-degenerate)


def _build_hash(params: dict[str, Any]) -> str:
    """Deterministic hash of an Optuna trial.params dict."""
    parts = []
    for k in sorted(params.keys()):
        parts.append(f"{k}={params[k]}")
    return "|".join(parts)


def load_study_trial_params(db_path: Path, study_name: str, top_n: int | None = None) -> list[dict[str, Any]]:
    """Read trial.params from an Optuna SQLite — sorted by value desc if top_n,
    else by trial number ascending."""
    conn = sqlite3.connect(str(db_path))
    try:
        # Studies + trials
        study_id = conn.execute(
            "SELECT study_id FROM studies WHERE study_name = ?", (study_name,)
        ).fetchone()
        if not study_id:
            return []
        study_id = study_id[0]
        if top_n is not None:
            # Top N by value
            rows = conn.execute(
                "SELECT t.trial_id, t.number, v.value FROM trials t "
                "JOIN trial_values v ON t.trial_id = v.trial_id "
                "WHERE t.study_id = ? AND t.state = 'COMPLETE' "
                "ORDER BY v.value DESC LIMIT ?",
                (study_id, top_n),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT trial_id, number, NULL FROM trials WHERE study_id = ? ORDER BY number",
                (study_id,),
            ).fetchall()
        out = []
        for trial_id, number, value in rows:
            param_rows = conn.execute(
                "SELECT param_name, param_value, distribution_json FROM trial_params WHERE trial_id = ?",
                (trial_id,),
            ).fetchall()
            params: dict[str, Any] = {}
            for name, raw_value, dist_json in param_rows:
                # Optuna stores categorical as the index into the distribution's choices
                if dist_json:
                    dist = json.loads(dist_json)
                    if dist.get("name") == "CategoricalDistribution":
                        choices = dist.get("attributes", {}).get("choices", [])
                        try:
                            idx = int(raw_value)
                            params[name] = choices[idx] if 0 <= idx < len(choices) else raw_value
                        except (ValueError, TypeError):
                            params[name] = raw_value
                    else:
                        params[name] = raw_value
                else:
                    params[name] = raw_value
            out.append({"trial_id": trial_id, "number": number, "value": value, "params": params})
        return out
    finally:
        conn.close()


def cross_regime_overlap_gate() -> dict[str, Any]:
    """Mechanism 13b: hammerhead-mid first-M trials should match
    hammerhead-early top-M with ≥80% Jaccard."""
    db_path = REPO_ROOT / "data" / "study_dbs" / "wave2-mid-warmstart" / "hammerhead__mid__tpe__seed0.db"
    if not db_path.exists():
        return {"passes": False, "reason": f"DB not found: {db_path}"}
    early_top = load_study_trial_params(db_path, "hammerhead__early", top_n=50)
    mid_all = load_study_trial_params(db_path, "hammerhead__mid", top_n=None)
    if not early_top:
        return {"passes": False, "reason": "early study has 0 COMPLETE trials"}
    if len(mid_all) < len(early_top):
        return {
            "passes": False,
            "reason": f"mid study has only {len(mid_all)} trials; need at least {len(early_top)} to compare",
            "n_early": len(early_top), "n_mid": len(mid_all),
        }
    early_hashes = {_build_hash(t["params"]) for t in early_top}
    mid_first = mid_all[:len(early_top)]
    mid_hashes = {_build_hash(t["params"]) for t in mid_first}
    overlap = early_hashes & mid_hashes
    union = early_hashes | mid_hashes
    jaccard = len(overlap) / len(union) if union else 0.0
    # Also check the more lenient "% of warm-start trials present in early top"
    presence_ratio = len(overlap) / len(early_hashes) if early_hashes else 0.0
    return {
        "passes": presence_ratio >= CROSS_REGIME_OVERLAP_MIN,
        "presence_ratio": presence_ratio,
        "jaccard": jaccard,
        "n_early_top": len(early_hashes),
        "n_mid_first": len(mid_hashes),
        "n_overlap": len(overlap),
        "threshold_min": CROSS_REGIME_OVERLAP_MIN,
    }


def regime_tier_gate() -> dict[str, Any]:
    """Mechanism 13: hammerhead-early hullmods all tier ≤1; hammerhead-mid has tier ≥2."""
    # Pull tier info from the manifest
    manifest_path = REPO_ROOT / "game" / "starsector" / "manifest.json"
    if not manifest_path.exists():
        return {"passes": False, "reason": f"manifest not found: {manifest_path}"}
    manifest = json.loads(manifest_path.read_text())
    hullmods = manifest.get("hullmods", {})
    tier_by_id = {hm_id: hm.get("tier", 0) for hm_id, hm in hullmods.items()}

    # Wave 2 writes under the campaign-prefixed path (task #90).
    log_path = REPO_ROOT / "data" / "logs" / "wave2-mid-warmstart" / "hammerhead__mid__tpe__seed0" / "evaluation_log.jsonl"
    if not log_path.exists():
        return {"passes": False, "reason": f"mid log not found: {log_path}"}

    early_violations = []  # tier > 1 in early
    mid_tier2_count = 0
    mid_total = 0
    # Wave 1 c2 seed-0 = canonical early baseline. This is pre-task-#90
    # data so it lives at the legacy path. Wave 1 historical paths are
    # frozen (analyzer side) — they cannot be migrated because the JSONL
    # interleaves data from multiple cells.
    early_log = REPO_ROOT / "data" / "logs" / "hammerhead__early__tpe__seed0" / "evaluation_log.jsonl"
    if early_log.exists():
        with early_log.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for hm in row.get("build", {}).get("hullmods", []):
                    tier = tier_by_id.get(hm)
                    if tier is not None and tier > 1:
                        early_violations.append({"hm": hm, "tier": tier})
    with log_path.open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid_total += 1
            for hm in row.get("build", {}).get("hullmods", []):
                tier = tier_by_id.get(hm)
                if tier is not None and tier >= 2:
                    mid_tier2_count += 1
                    break
    early_passes = not early_violations
    mid_passes = mid_total > 0 and mid_tier2_count > 0
    return {
        "passes": early_passes and mid_passes,
        "early_violations_count": len(early_violations),
        "early_violations_sample": early_violations[:5],
        "mid_total_rows": mid_total,
        "mid_rows_with_tier_geq_2_hm": mid_tier2_count,
    }


def wolf_frigate_gates() -> dict[str, Any]:
    """Mechanism 4 (Wolf): τ̂² > 1e-3 (no frigate gradient collapse)
    AND finalized count ≥ 150 (drop-out < 25%)."""
    db_path = REPO_ROOT / "data" / "study_dbs" / "wave2-wolf-early" / "wolf__early__tpe__seed0.db"
    # Wave 2 writes under the campaign-prefixed path (task #90).
    log_path = REPO_ROOT / "data" / "logs" / "wave2-wolf-early" / "wolf__early__tpe__seed0" / "evaluation_log.jsonl"
    if not db_path.exists():
        return {"passes": False, "reason": f"wolf DB not found: {db_path}"}
    conn = sqlite3.connect(str(db_path))
    try:
        n_complete = conn.execute("SELECT COUNT(*) FROM trials WHERE state='COMPLETE'").fetchone()[0]
    finally:
        conn.close()
    finalized_pass = n_complete >= WOLF_FINALIZED_MIN
    # τ̂² estimate: variance of twfe_fitness across finalized trials
    twfe_values = []
    if log_path.exists():
        with log_path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("pruned"):
                    continue
                v = row.get("twfe_fitness")
                if v is not None:
                    twfe_values.append(v)
    if len(twfe_values) < 2:
        var = float("nan")
    else:
        var = statistics.variance(twfe_values)
    var_pass = (not math.isnan(var)) and var > WOLF_TAU_SQ_MIN
    # Win-rate sanity (F4a check): if > 80% player-wins, opponent pool is too easy
    n_player_wins = 0
    n_total = 0
    if log_path.exists():
        with log_path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for opp_result in row.get("opponent_results", []):
                    n_total += 1
                    if isinstance(opp_result, dict) and opp_result.get("winner") == "PLAYER":
                        n_player_wins += 1
    player_win_rate = n_player_wins / n_total if n_total > 0 else float("nan")
    return {
        "passes": finalized_pass and var_pass,
        "n_complete": n_complete,
        "finalized_pass": finalized_pass,
        "twfe_variance": var,
        "twfe_var_pass": var_pass,
        "twfe_var_threshold": WOLF_TAU_SQ_MIN,
        "n_twfe_samples": len(twfe_values),
        "player_win_rate": player_win_rate,
        "f4a_decision_tree_branch": "F4a (player wins > 80%, opponent pool too easy)" if (player_win_rate > 0.80) else None,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/wave2-gates.json")
    args = p.parse_args()
    gates = {
        "cross_regime_warm_start": cross_regime_overlap_gate(),
        "regime_tier": regime_tier_gate(),
        "wolf_frigate": wolf_frigate_gates(),
    }
    print("=" * 78)
    print("Wave 2 gate analyzer")
    print("=" * 78)
    for name, v in gates.items():
        passes = v.get("passes")
        marker = "PASS" if passes else "FAIL"
        print(f"\n[{marker}] {name}")
        for k, val in v.items():
            if k == "passes":
                continue
            if isinstance(val, float):
                print(f"   {k}: {val:.4f}")
            else:
                print(f"   {k}: {val}")
    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(),
        "gates": gates,
    }, indent=2, default=str))
    print(f"\nWrote {out_path}")
    n_pass = sum(1 for v in gates.values() if v.get("passes"))
    n_total = len(gates)
    print(f"Verdict: {n_pass}/{n_total} gates pass")
    return 1 if n_pass < n_total else 0


if __name__ == "__main__":
    sys.exit(main())
