"""Tests for scripts/analysis/accounting_extract.py (roadmap item 3)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "accounting_extract.py"
_spec = importlib.util.spec_from_file_location("_accounting_extract", _SCRIPT)
assert _spec is not None and _spec.loader is not None
acct = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("_accounting_extract", acct)
_spec.loader.exec_module(acct)


def _row(trial_number, *, evaluated, dispatched, pruned=False, cache_hit=False, invalid_spec=False):
    return {
        "trial_number": trial_number,
        "opponents_evaluated": evaluated,
        "matchups_dispatched": dispatched,
        "pruned": pruned,
        "cache_hit": cache_hit,
        "invalid_spec": invalid_spec,
    }


class TestClassifyAndRecords:
    def test_kind_partition(self):
        rows = [
            _row(0, evaluated=10, dispatched=11),  # completed (1 retry)
            _row(1, evaluated=3, dispatched=3, pruned=True),  # pruned
            _row(2, evaluated=0, dispatched=0, cache_hit=True),  # cache hit
            _row(3, evaluated=0, dispatched=0, invalid_spec=True),  # invalid spec
        ]
        recs = acct.records_from_eval_rows(rows)
        kinds = {r.trial_number: r.kind for r in recs}
        assert kinds == {0: "completed", 1: "pruned", 2: "cache_hit", 3: "invalid_spec"}

    def test_dispatched_geq_evaluated_captures_retries(self):
        recs = acct.records_from_eval_rows([_row(0, evaluated=10, dispatched=13)])
        assert recs[0].matchups_dispatched - recs[0].opponents_evaluated == 3


class TestSummarize:
    def test_totals_and_partition(self):
        recs = [
            acct.TrialRecord(0, "completed", 10, 11),
            acct.TrialRecord(1, "pruned", 3, 3),
            acct.TrialRecord(2, "instance_error", 0, 2),
        ]
        s = acct.summarize(recs)
        # Aggregate matchup total = Σ dispatched (the missing campaign counter).
        assert s["total_matchups_dispatched"] == 16
        assert s["total_opponents_evaluated"] == 13
        assert s["by_kind"]["completed"]["count"] == 1
        assert s["by_kind"]["instance_error"]["matchups_dispatched"]["total"] == 2
        assert s["by_kind"]["cache_hit"]["count"] == 0

    def test_pruning_depth_is_total_minus_evaluated(self):
        # A pruned trial dispatched 5, scored 3 → pruning depth 2 (via the fields).
        recs = [acct.TrialRecord(0, "pruned", 3, 5)]
        s = acct.summarize(recs)
        r = s["by_kind"]["pruned"]
        assert r["matchups_dispatched"]["total"] - r["opponents_evaluated"]["total"] == 2


class TestInstanceErrorRecovery:
    def _study_db(self, tmp_path, trials):
        """trials: list of (number, state, matchups_dispatched|None) or
        (number, state, matchups_dispatched|None, terminal_reason|None)."""
        db = tmp_path / "s.db"
        con = sqlite3.connect(db)
        con.execute("create table trials (trial_id int, number int, state text)")
        con.execute(
            "create table trial_user_attributes "
            "(trial_user_attr_id int, trial_id int, key text, value_json text)"
        )
        uaid = 0
        for tid, row in enumerate(trials):
            number, state, disp = row[0], row[1], row[2]
            reason = row[3] if len(row) > 3 else None
            con.execute("insert into trials values (?, ?, ?)", (tid, number, state))
            if disp is not None:
                con.execute(
                    "insert into trial_user_attributes values (?, ?, ?, ?)",
                    (uaid, tid, "matchups_dispatched", json.dumps(disp)),
                )
                uaid += 1
            if reason is not None:
                con.execute(
                    "insert into trial_user_attributes values (?, ?, ?, ?)",
                    (uaid, tid, "terminal_reason", json.dumps(reason)),
                )
                uaid += 1
        con.commit()
        con.close()
        return db

    def test_recovers_terminal_failure_trials_absent_from_log(self, tmp_path):
        # Trial 0 is logged (finalized, no user_attr); trial 99 is a terminal-
        # failure trial (COMPLETE, user_attr set, NOT in the log). Absent
        # terminal_reason defaults to instance_error (backward-compat).
        db = self._study_db(tmp_path, [(0, "COMPLETE", None), (99, "COMPLETE", 2)])
        recs = acct.read_terminal_failure_records(db, logged_trial_numbers={0})
        assert len(recs) == 1
        assert recs[0].trial_number == 99
        assert recs[0].kind == "instance_error"
        assert recs[0].matchups_dispatched == 2

    def test_partitions_by_terminal_reason(self, tmp_path):
        # An instance_error and a worker_timeout trial partition into two kinds.
        db = self._study_db(
            tmp_path,
            [
                (10, "COMPLETE", 3, "instance_error"),
                (11, "COMPLETE", 5, "worker_timeout"),
            ],
        )
        recs = acct.read_terminal_failure_records(db, logged_trial_numbers=set())
        by_num = {r.trial_number: r.kind for r in recs}
        assert by_num == {10: "instance_error", 11: "worker_timeout"}

    def test_logged_trial_with_attr_is_not_double_counted(self, tmp_path):
        # Defensive: a trial present in the log is never treated as a failure.
        db = self._study_db(tmp_path, [(5, "COMPLETE", 7)])
        recs = acct.read_terminal_failure_records(db, logged_trial_numbers={5})
        assert recs == []


class TestExtractCell:
    def test_merges_log_and_instance_errors(self, tmp_path):
        log = tmp_path / "evaluation_log.jsonl"
        log.write_text(
            "\n".join(json.dumps(r) for r in [_row(0, evaluated=10, dispatched=10)]) + "\n"
        )
        db = tmp_path / "s.db"
        con = sqlite3.connect(db)
        con.execute("create table trials (trial_id int, number int, state text)")
        con.execute(
            "create table trial_user_attributes "
            "(trial_user_attr_id int, trial_id int, key text, value_json text)"
        )
        con.execute("insert into trials values (0, 0, 'COMPLETE')")
        con.execute("insert into trials values (1, 42, 'COMPLETE')")
        con.execute("insert into trial_user_attributes values (0, 1, 'matchups_dispatched', '4')")
        con.commit()
        con.close()
        recs = acct.extract_cell(log, db)
        kinds = sorted(r.kind for r in recs)
        assert kinds == ["completed", "instance_error"]
        assert acct.summarize(recs)["total_matchups_dispatched"] == 14
