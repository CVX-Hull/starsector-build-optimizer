"""Wave 1 progress dashboard. Quick re-check during the multi-hour run.

Reads SQLite + ledger directly — no AWS calls, no provider creds needed.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CELLS = ("c0a", "c0b", "c1", "c2", "c3")
SEEDS = (0, 1, 2)


def cell_status(cell: str) -> dict:
    db_dir = REPO_ROOT / "data" / "study_dbs" / f"wave1-{cell}"
    if not db_dir.exists():
        return {"status": "not started"}
    out = {"status": "running", "seeds": {}}
    for seed in SEEDS:
        db = db_dir / f"hammerhead__early__tpe__seed{seed}.db"
        if not db.exists():
            out["seeds"][seed] = "missing"
            continue
        conn = sqlite3.connect(str(db))
        try:
            n_complete = conn.execute("SELECT COUNT(*) FROM trials WHERE state='COMPLETE'").fetchone()[0]
            n_pruned = conn.execute("SELECT COUNT(*) FROM trials WHERE state='PRUNED'").fetchone()[0]
            n_running = conn.execute("SELECT COUNT(*) FROM trials WHERE state='RUNNING'").fetchone()[0]
            n_fail = conn.execute("SELECT COUNT(*) FROM trials WHERE state='FAIL'").fetchone()[0]
        finally:
            conn.close()
        out["seeds"][seed] = {
            "complete": n_complete, "pruned": n_pruned,
            "running": n_running, "fail": n_fail,
        }
    # Cost
    ledger = REPO_ROOT / "data" / "campaigns" / f"wave1-{cell}" / "ledger.jsonl"
    cost = 0.0
    if ledger.exists():
        with ledger.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("event_type") == "worker_heartbeat":
                    cost = max(cost, d.get("cumulative_usd", 0))
    out["cost_usd"] = cost
    # Mismatch count
    olog = REPO_ROOT / "data" / "campaigns" / f"wave1-{cell}" / "orchestrator.log"
    raw_mismatch = 0
    discarding = 0
    ok_count = 0
    if olog.exists():
        with olog.open(errors="replace") as f:
            for line in f:
                if "discarding LOADOUT_MISMATCH" in line:
                    discarding += 1
                    raw_mismatch += 1  # paired _log_loadout line
                elif "LOADOUT_MISMATCH" in line:
                    raw_mismatch += 1
                elif "LOADOUT_OK" in line:
                    ok_count += 1
    # Post-band-aid: mismatch events count 2 lines each; use `discarding` count
    mismatch = discarding if discarding > 0 else raw_mismatch
    out["loadout_mismatch"] = mismatch
    out["loadout_ok"] = ok_count
    out["mismatch_rate"] = mismatch / max(1, ok_count + mismatch)
    return out


def main() -> int:
    total_cost = 0.0
    total_mismatch = 0
    total_ok = 0
    print("Wave 1 status — " + str(REPO_ROOT))
    print("=" * 78)
    for cell in CELLS:
        s = cell_status(cell)
        if s.get("status") == "not started":
            print(f"  {cell}: not started")
            continue
        seeds_str_parts = []
        for seed in SEEDS:
            v = s["seeds"].get(seed, "?")
            if isinstance(v, dict):
                fin = v["complete"] + v["pruned"]
                seeds_str_parts.append(f"s{seed}: {fin}f/{v['running']}r")
            else:
                seeds_str_parts.append(f"s{seed}: {v}")
        cost = s["cost_usd"]
        mr = s["mismatch_rate"] * 100
        print(
            f"  {cell}: ${cost:.2f}  {' | '.join(seeds_str_parts)}  "
            f"loadout mismatch {s['loadout_mismatch']}/{s['loadout_ok']+s['loadout_mismatch']} ({mr:.2f}%)"
        )
        total_cost += cost
        total_mismatch += s["loadout_mismatch"]
        total_ok += s["loadout_ok"]
    print("-" * 78)
    overall_mr = total_mismatch / max(1, total_ok + total_mismatch) * 100
    print(
        f"  TOTAL: ${total_cost:.2f}  "
        f"loadout mismatch {total_mismatch}/{total_ok+total_mismatch} ({overall_mr:.2f}%)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
