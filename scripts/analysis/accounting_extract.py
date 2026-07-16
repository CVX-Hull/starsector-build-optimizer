#!/usr/bin/env python3
"""Matchups-per-trial accounting extractor (roadmap item 3).

Offline, no sim spend. Reads an instrumented run's eval logs + study DBs and
emits the matchups-per-trial spread: per-trial `opponents_evaluated` (scored,
useful work) and `matchups_dispatched` (dispatched incl. retries, the cost
basis), partitioned by trial kind, per cell and pooled, plus the aggregate
matchup total.

Trial kinds (spec 24): `completed`, `pruned`, `cache_hit`, `invalid_spec` come
from the eval-log rows. The two **terminal-failure** kinds — `instance_error`
(worker/instance death) and `worker_timeout` (a dispatched matchup returned no
result within `result_timeout_seconds` after exhausting retries) — emit NO
eval-log row (they would orphan the replay's bijective join) and are recovered
from the study DB as `state=COMPLETE` trials carrying a `matchups_dispatched`
user_attr but absent from the eval log; the `terminal_reason` user_attr (set only
by the two terminal finalizers) partitions them. See docs/specs/24-optimizer.md
"the fifth path".
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median

# The two terminal-failure kinds a trial's `terminal_reason` user_attr can carry
# (set only by optimizer._finalize_terminal_failure); an unrecognized value
# collapses to instance_error.
_TERMINAL_KINDS = ("instance_error", "worker_timeout")
_KINDS = ("completed", "pruned", "cache_hit", "invalid_spec", *_TERMINAL_KINDS)


@dataclass(frozen=True)
class TrialRecord:
    """One trial's accounting facts."""

    trial_number: int
    kind: str
    opponents_evaluated: int
    matchups_dispatched: int


def classify_row(row: dict) -> str:
    """Map an eval-log row to one of the four logged kinds."""
    if row.get("pruned"):
        return "pruned"
    if row.get("cache_hit"):
        return "cache_hit"
    if row.get("invalid_spec"):
        return "invalid_spec"
    return "completed"


def records_from_eval_rows(rows: list[dict]) -> list[TrialRecord]:
    """Per-trial records for the four eval-log-bearing kinds."""
    out = []
    for row in rows:
        out.append(
            TrialRecord(
                trial_number=int(row["trial_number"]),
                kind=classify_row(row),
                opponents_evaluated=int(row.get("opponents_evaluated", 0)),
                matchups_dispatched=int(row.get("matchups_dispatched", 0)),
            )
        )
    return out


def summarize(records: list[TrialRecord]) -> dict:
    """Distributions of dispatched + scored matchups, partitioned by kind,
    plus the aggregate total. Pure — no I/O."""

    def _dist(values: list[int]) -> dict:
        if not values:
            return {"n": 0, "min": None, "median": None, "mean": None, "max": None, "total": 0}
        return {
            "n": len(values),
            "min": min(values),
            "median": median(values),
            "mean": sum(values) / len(values),
            "max": max(values),
            "total": sum(values),
        }

    by_kind = {}
    for kind in _KINDS:
        krecs = [r for r in records if r.kind == kind]
        by_kind[kind] = {
            "count": len(krecs),
            "matchups_dispatched": _dist([r.matchups_dispatched for r in krecs]),
            "opponents_evaluated": _dist([r.opponents_evaluated for r in krecs]),
        }
    return {
        "n_trials": len(records),
        "total_matchups_dispatched": sum(r.matchups_dispatched for r in records),
        "total_opponents_evaluated": sum(r.opponents_evaluated for r in records),
        "by_kind": by_kind,
        "overall_matchups_dispatched": _dist([r.matchups_dispatched for r in records]),
    }


def read_eval_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_terminal_failure_records(
    study_db: Path, logged_trial_numbers: set[int]
) -> list[TrialRecord]:
    """Terminal-failure trials: COMPLETE in the study DB, carrying a
    `matchups_dispatched` user_attr (set only by the two terminal finalizers),
    and absent from the eval log. Returns their records, partitioned by the
    `terminal_reason` user_attr into `instance_error` vs `worker_timeout`
    (absent → `instance_error` for backward-compat with pre-discriminator runs).

    `opponents_evaluated` is set to 0: any matchups such a trial scored before
    failing never entered the corpus (spec 24 "the fifth path" — no eval-log/DB
    row), so 0 is the *corpus useful-work* count. The dispatched count (from the
    user_attr) is the honest cost basis and is exact."""
    con = sqlite3.connect(study_db)
    try:
        rows = con.execute(
            """
            select t.number, ua.value_json,
                   (select tr.value_json from trial_user_attributes tr
                    where tr.trial_id = t.trial_id and tr.key = 'terminal_reason')
            from trials t
            join trial_user_attributes ua on ua.trial_id = t.trial_id
            where t.state = 'COMPLETE' and ua.key = 'matchups_dispatched'
            """
        ).fetchall()
    finally:
        con.close()
    out = []
    for number, value_json, reason_json in rows:
        n = int(number)
        if n in logged_trial_numbers:
            continue  # defensive: a logged trial is not a terminal-failure trial
        reason = json.loads(reason_json) if reason_json is not None else "instance_error"
        kind = reason if reason in _TERMINAL_KINDS else "instance_error"
        out.append(
            TrialRecord(
                trial_number=n,
                kind=kind,
                opponents_evaluated=0,
                matchups_dispatched=int(json.loads(value_json)),
            )
        )
    return out


def extract_cell(eval_log: Path, study_db: Path | None) -> list[TrialRecord]:
    """All trial records for one cell: eval-log kinds + terminal-failure trials
    (instance_error / worker_timeout, recovered from the study DB)."""
    rows = read_eval_log(eval_log)
    records = records_from_eval_rows(rows)
    if study_db is not None and study_db.exists():
        logged = {r.trial_number for r in records}
        records = records + read_terminal_failure_records(study_db, logged)
    return records


def _study_db_for(eval_log: Path, study_db_root: Path) -> Path:
    # data/logs/<campaign>/<study>/evaluation_log.jsonl → <root>/<campaign>/<study>.db
    campaign, study = eval_log.parts[-3], eval_log.parts[-2]
    return study_db_root / campaign / f"{study}.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-base-dir", type=Path, default=Path("data/logs"))
    parser.add_argument("--campaign", required=True, help="campaign name (dir under log-base-dir)")
    parser.add_argument("--study-db-root", type=Path, default=Path("data/study_dbs"))
    parser.add_argument("--output", type=Path, default=None, help="JSON output path (else stdout)")
    args = parser.parse_args(argv)

    cell_logs = sorted((args.log_base_dir / args.campaign).glob("*/evaluation_log.jsonl"))
    if not cell_logs:
        parser.error(f"no eval logs under {args.log_base_dir / args.campaign}")

    per_cell = {}
    pooled: list[TrialRecord] = []
    for eval_log in cell_logs:
        recs = extract_cell(eval_log, _study_db_for(eval_log, args.study_db_root))
        per_cell[eval_log.parts[-2]] = summarize(recs)
        pooled.extend(recs)

    result = {
        "campaign": args.campaign,
        "n_cells": len(cell_logs),
        "pooled": summarize(pooled),
        "per_cell": per_cell,
    }
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
