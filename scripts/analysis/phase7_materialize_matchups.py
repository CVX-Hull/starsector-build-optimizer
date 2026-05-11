#!/usr/bin/env python
"""Materialize Phase 7 prior-run matchup data into a derived SQLite DB."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from starsector_optimizer.game_manifest import GameManifest
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.phase7_matchup_data import (
    honest_build_id_to_key,
    iter_honest_eval_matchups,
    iter_training_matchups,
    materialize_sqlite,
    recover_honest_eval_candidate_builds,
    recover_honest_eval_output_builds,
    recover_logged_builds,
    recover_study_db_builds,
)

DEFAULT_HONEST_TOP_K = 10


def _expand_patterns(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches and Path(pattern).exists():
            matches = [pattern]
        paths.extend(Path(match) for match in matches)
    return sorted(dict.fromkeys(paths))


def _parse_db_spec(value: str) -> tuple[Path, str, str | None, str | None, int | None]:
    parts = value.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            "DB spec must be path:hull_id[:campaign[:study[:seed]]]"
        )
    seed = int(parts[4]) if len(parts) > 4 and parts[4] else None
    return (
        Path(parts[0]),
        parts[1],
        parts[2] if len(parts) > 2 and parts[2] else None,
        parts[3] if len(parts) > 3 and parts[3] else None,
        seed,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a generated Phase 7 SQLite matchup dataset."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/phase7/matchups.sqlite"),
        help="Derived SQLite output path.",
    )
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=Path("game/starsector"),
        help="Game root containing data/ and variants.",
    )
    parser.add_argument(
        "--log-glob",
        action="append",
        default=[],
        help="Optimizer evaluation_log.jsonl path or glob. May be repeated.",
    )
    parser.add_argument(
        "--study-db",
        action="append",
        default=[],
        type=_parse_db_spec,
        metavar="PATH:HULL[:CAMPAIGN[:STUDY[:SEED]]]",
        help="Optuna DB recovery source. May be repeated.",
    )
    parser.add_argument(
        "--honest-ledger",
        type=Path,
        default=None,
        help="Optional honest-eval JSONL ledger to materialize.",
    )
    parser.add_argument(
        "--honest-candidate-log-glob",
        action="append",
        default=[],
        help="Candidate-selection evaluation_log.jsonl path/glob for honest ledger build IDs.",
    )
    parser.add_argument(
        "--honest-eval-json-glob",
        action="append",
        default=[],
        help=(
            "Completed data/campaigns/*/honest_eval.json path/glob. Use this "
            "to recover evaluator-generated builds such as random-baseline."
        ),
    )
    parser.add_argument(
        "--honest-hull-id",
        default=None,
        help="Hull id for honest candidate reconstruction.",
    )
    parser.add_argument("--honest-top-k", type=int, default=DEFAULT_HONEST_TOP_K)
    parser.add_argument("--honest-method", default="twfe_eb")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    log_paths = _expand_patterns(args.log_glob)
    honest_candidate_paths = _expand_patterns(args.honest_candidate_log_glob)
    honest_eval_json_paths = _expand_patterns(args.honest_eval_json_glob)

    game_data = load_game_data(args.game_dir)
    manifest = GameManifest.load(args.game_dir / "manifest.json")

    recovered = list(recover_logged_builds(log_paths))
    training_matchups = list(iter_training_matchups(log_paths))

    for db_path, hull_id, campaign, study, seed in args.study_db:
        hull = game_data.hulls[hull_id]
        recovered.extend(
            recover_study_db_builds(
                db_path,
                hull,
                game_data,
                manifest,
                campaign=campaign,
                study=study,
                seed=seed,
            )
        )

    honest_matchups = []
    if args.honest_ledger:
        if honest_candidate_paths and not args.honest_hull_id:
            raise SystemExit("--honest-hull-id is required with honest candidate logs")
        build_id_to_key = {}
        if honest_candidate_paths:
            hull = game_data.hulls[args.honest_hull_id]
            honest_candidates = recover_honest_eval_candidate_builds(
                honest_candidate_paths,
                hull,
                game_data,
                manifest,
                top_k=args.honest_top_k,
                method=args.honest_method,
            )
            recovered.extend(honest_candidates)
            build_id_to_key = honest_build_id_to_key(honest_candidates)
        if honest_eval_json_paths:
            honest_outputs = recover_honest_eval_output_builds(honest_eval_json_paths)
            recovered.extend(honest_outputs)
            build_id_to_key.update(honest_build_id_to_key(honest_outputs))
        honest_matchups = list(
            iter_honest_eval_matchups(args.honest_ledger, build_id_to_key)
        )
        unresolved_honest_build_ids = sorted(
            {row.build_id for row in honest_matchups if row.build_key is None}
        )

    materialize_sqlite(
        args.output,
        recovered_builds=recovered,
        training_matchups=training_matchups,
        honest_eval_matchups=honest_matchups,
    )
    print(json.dumps(
        {
            "output": str(args.output),
            "recovered_builds": len(recovered),
            "training_matchups": len(training_matchups),
            "honest_eval_matchups": len(honest_matchups),
            "unresolved_honest_build_ids": (
                len(unresolved_honest_build_ids) if args.honest_ledger else 0
            ),
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
