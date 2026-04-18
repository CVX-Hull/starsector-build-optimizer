#!/usr/bin/env python3
"""Compute throughput + cost-per-matchup from a cloud benchmark run.

Reads:
  results-<instance>/metadata.json  — start/end epoch, instance type
  results-<instance>/sar.log        — per-second CPU idle %
  results-<instance>/data/evaluation_log.jsonl — per-matchup records

Emits: markdown-ready stats.
"""
import json
import re
import sys
from pathlib import Path
from statistics import mean


SPOT_PRICE = {
    "c7i.2xlarge": 0.158,
    "c7i.4xlarge": 0.267,
    "g6.xlarge":   0.365,
    "ccx33":       0.13,  # Hetzner dedicated 8 vCPU, Ashburn VA
}


def parse_sar(path: Path) -> dict:
    """Extract mean %usr and %idle from sar -u output."""
    if not path.exists():
        return {"samples": 0, "mean_idle": None, "mean_usr": None}
    idle, usr = [], []
    for line in path.read_text().splitlines():
        m = re.match(r"^\d\d:\d\d:\d\d\s+all\s+(\S+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)$", line)
        if m:
            try:
                u = float(m.group(1)); i = float(m.group(2))
                usr.append(u); idle.append(i)
            except ValueError:
                continue
    if not idle:
        return {"samples": 0, "mean_idle": None, "mean_usr": None}
    return {"samples": len(idle), "mean_idle": mean(idle), "mean_usr": mean(usr)}


def parse_eval_log(path: Path) -> dict:
    """Count trials + sum matchups from evaluation_log.jsonl.

    Record layout: one JSON per completed build (trial); each has an
    `opponent_results` array of per-matchup dicts with `duration_seconds`.
    """
    if not path.exists():
        return {"trials": 0, "matchups": 0, "durations": [], "pruned": 0}
    trials, matchups, pruned, durations = 0, 0, 0, []
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        trials += 1
        if rec.get("pruned"):
            pruned += 1
        for m in rec.get("opponent_results", []):
            matchups += 1
            if "duration_seconds" in m:
                durations.append(m["duration_seconds"])
    return {"trials": trials, "matchups": matchups, "durations": durations, "pruned": pruned}


def analyze(results_dir: Path) -> dict:
    meta = json.loads((results_dir / "metadata.json").read_text())
    sar = parse_sar(results_dir / "sar.log")
    evl = parse_eval_log(results_dir / "data" / "evaluation_log.jsonl")
    elapsed_s = meta["elapsed_seconds"]
    elapsed_h = elapsed_s / 3600
    n_inst = meta["num_instances"]
    it = meta["instance_type"]
    price = SPOT_PRICE.get(it, None)
    cost = price * elapsed_h if price else None
    matchups_per_hr_per_inst = (evl["matchups"] / elapsed_h / n_inst) if elapsed_h and n_inst else None
    trials_per_hr = (evl["trials"] / elapsed_h) if elapsed_h else None
    return {
        **meta,
        "elapsed_hours": round(elapsed_h, 3),
        "sar": sar,
        "matchups_total": evl["matchups"],
        "trials_total": evl["trials"],
        "trials_pruned": evl["pruned"],
        "trials_per_hour": round(trials_per_hr, 2) if trials_per_hr else None,
        "matchups_per_hour_per_instance": round(matchups_per_hr_per_inst, 2) if matchups_per_hr_per_inst else None,
        "mean_matchup_duration_s": round(mean(evl["durations"]), 1) if evl["durations"] else None,
        "cost_usd": round(cost, 4) if cost else None,
        "cost_per_matchup_usd": round(cost / evl["matchups"], 5) if cost and evl["matchups"] else None,
    }


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results_dir>")
        sys.exit(1)
    results = analyze(Path(sys.argv[1]))
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
