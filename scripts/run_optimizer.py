#!/usr/bin/env python3
"""Run ship build optimization for a single hull."""

import argparse
import logging
import os
import signal
import sys

sys.path.insert(0, "src")

from pathlib import Path

from starsector_optimizer.game_manifest import GameManifest
from starsector_optimizer.models import REGIME_PRESETS
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.instance_manager import InstanceConfig, LocalInstancePool
from starsector_optimizer.opponent_pool import discover_opponent_pool, get_opponents
from starsector_optimizer.optimizer import OptimizerConfig, optimize_hull


def _install_signal_handlers() -> None:
    """Route SIGTERM/SIGHUP to KeyboardInterrupt so `with LocalInstancePool(...)`
    unwinds cleanly and game JVMs + Xvfb don't orphan.

    SIGINT already raises KeyboardInterrupt by default (Ctrl-C works).
    """
    def handler(signum, _frame):
        raise KeyboardInterrupt(f"received signal {signum}")
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGHUP, handler)


def main():
    parser = argparse.ArgumentParser(description="Optimize a ship build")
    parser.add_argument("--hull", required=True, help="Hull ID to optimize (e.g. wolf, eagle)")
    parser.add_argument("--game-dir", required=True, type=Path, help="Path to Starsector installation")
    parser.add_argument("--num-instances", type=int, default=2, help="Parallel game instances")
    parser.add_argument("--sim-budget", type=int, default=50, help="Number of build evaluations")
    parser.add_argument("--study-db", type=str, default=None, help="SQLite path for study persistence")
    parser.add_argument("--sampler", choices=["tpe"], default="tpe",
                        help="Optimization sampler: tpe (only option on this "
                             "codebase; see optimizer.py docstring)")
    parser.add_argument("--heuristic-only", action="store_true",
                        help="Use heuristic score instead of simulation (for testing)")
    parser.add_argument("--analyze-importance", action="store_true",
                        help="Run fANOVA importance analysis on existing study and exit")
    parser.add_argument("--active-opponents", type=int, default=10,
                        help="Max opponents per build (default 10, selects top-K from pool)")
    parser.add_argument("--fix-params", type=Path, default=None,
                        help="JSON file mapping param names to fixed values")
    parser.add_argument("--regime", choices=list(REGIME_PRESETS.keys()), default="early",
                        help="Loadout regime. Filters hullmods/weapons by component "
                             "availability. Default 'early' is the most conservative "
                             "filter (tier<=1, no rare blueprints); opt up with "
                             "'mid'/'late'/'endgame' as the save's unlocked inventory "
                             "grows. See docs/reference/phase5f-*.md.")
    parser.add_argument("--warm-start-from-regime",
                        choices=list(REGIME_PRESETS.keys()), default=None,
                        help="Name of a prior regime whose top trials will be "
                             "enqueued as warm-start seeds. Requires the prior "
                             "study to exist in the same --study-db. Typos fail "
                             "at argparse rather than at study-load time.")
    parser.add_argument("--worker-pool", choices=["local", "cloud"], default="local",
                        help="Where workers live. 'local' uses LocalInstancePool "
                             "(JVMs on this host); 'cloud' uses CloudWorkerPool "
                             "(workstation-as-orchestrator, AWS spot VMs; see "
                             "docs/specs/22-cloud-deployment.md).")
    parser.add_argument("--campaign-config", type=Path, default=None,
                        help="Campaign YAML path. Required when --worker-pool=cloud.")
    parser.add_argument("--study-idx", type=int, default=0,
                        help="Study index within the campaign (indexes "
                             "campaign.studies[]). Required when --worker-pool=cloud.")
    parser.add_argument("--seed-idx", type=int, default=0,
                        help="Seed index within the selected study's seeds tuple. "
                             "Required when --worker-pool=cloud; avoids the "
                             "flat-idx bug when a study has >1 seed.")
    args = parser.parse_args()

    if (args.warm_start_from_regime is not None
            and args.warm_start_from_regime == args.regime):
        parser.error(
            f"--warm-start-from-regime={args.warm_start_from_regime} "
            f"cannot equal --regime={args.regime} (self-seeding is handled "
            f"by Optuna's load_if_exists; specify a different source regime)"
        )

    _install_signal_handlers()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print(f"Loading game data from {args.game_dir}...")
    game_data = load_game_data(args.game_dir)
    manifest = GameManifest.load()
    print(
        f"Loaded {len(game_data.hulls)} hulls, {len(game_data.weapons)} weapons; "
        f"manifest schema_v{manifest.constants.manifest_schema_version} "
        f"(mod_commit_sha={manifest.constants.mod_commit_sha[:8] or '<empty>'})"
    )

    if args.hull not in game_data.hulls:
        print(f"Error: hull '{args.hull}' not found. Available: {sorted(game_data.hulls.keys())[:10]}...")
        sys.exit(1)

    hull = game_data.hulls[args.hull]
    opponent_pool = discover_opponent_pool(args.game_dir, game_data)
    opponents = get_opponents(opponent_pool, hull.hull_size)
    print(f"Hull size: {hull.hull_size.name}, opponents: {len(opponents)}")

    storage = f"sqlite:///{args.study_db}" if args.study_db else None

    # Load fixed params if provided
    fixed_params = None
    if args.fix_params:
        import json
        with open(args.fix_params) as f:
            fixed_params = json.load(f)
        if not isinstance(fixed_params, dict):
            print("Error: --fix-params JSON must be a dict mapping param names to values")
            sys.exit(1)
        print(f"Fixed params: {len(fixed_params)} parameters frozen")

    # Analyze importance mode
    if args.analyze_importance:
        if not args.study_db:
            print("Error: --analyze-importance requires --study-db")
            sys.exit(1)
        import optuna
        from starsector_optimizer.importance import analyze_importance, print_importance_report
        study = optuna.load_study(
            study_name=f"{args.hull}__{args.regime}", storage=storage,
        )
        result = analyze_importance(study)
        print(print_importance_report(result))
        return

    # Per-study eval log directory. The row schema does not carry study_id /
    # sampler, so writing all studies to one file destroys per-study
    # attribution under concurrent-subprocess dispatch (e.g. any campaign with
    # multiple studies in parallel). One dir per (hull, regime, sampler,
    # seed_idx) — uniform for local and cloud runs.
    eval_log_dir = Path(
        f"data/logs/{args.hull}__{args.regime}__{args.sampler}__seed_idx{args.seed_idx}"
    )
    eval_log_dir.mkdir(parents=True, exist_ok=True)
    eval_log_path = eval_log_dir / "evaluation_log.jsonl"

    config = OptimizerConfig(
        sim_budget=args.sim_budget,
        sampler=args.sampler,
        active_opponents=args.active_opponents,
        fixed_params=fixed_params,
        study_storage=storage,
        eval_log_path=eval_log_path,
        regime=REGIME_PRESETS[args.regime],
        warm_start_from_regime=args.warm_start_from_regime,
    )

    if args.heuristic_only:
        print("Heuristic-only mode: warm-start only, no simulation.")
        import optuna
        from starsector_optimizer.optimizer import (
            _enqueue_warm_start_from_regime, warm_start,
        )
        from starsector_optimizer.search_space import build_search_space
        study = optuna.create_study(
            direction="maximize", storage=storage,
            study_name=f"{args.hull}__{args.regime}", load_if_exists=True,
        )
        if args.warm_start_from_regime is not None:
            if storage is None:
                parser.error(
                    "--warm-start-from-regime requires --study-db (source "
                    "and target studies must share one SQLite backend)."
                )
            space = build_search_space(hull, game_data, config.regime, manifest)
            _enqueue_warm_start_from_regime(
                target_study=study,
                source_storage=storage,
                source_study_name=f"{args.hull}__{args.warm_start_from_regime}",
                target_regime=config.regime,
                top_m=config.warm_start_n,
                hull=hull,
                game_data=game_data,
                manifest=manifest,
                target_space=space,
            )
        warm_start(study, hull, game_data, config, manifest)
        _print_results(study, args.hull, game_data)
        return

    if args.worker_pool == "cloud":
        if args.campaign_config is None:
            parser.error("--worker-pool=cloud requires --campaign-config")
        from starsector_optimizer.cloud_runner import run_cloud_study
        try:
            study = run_cloud_study(
                campaign_yaml_path=args.campaign_config,
                study_idx=args.study_idx,
                seed_idx=args.seed_idx,
                hull_id=args.hull,
                hull=hull,
                game_data=game_data,
                manifest=manifest,
                opponent_pool=opponent_pool,
                optimizer_config=config,
            )
        except KeyboardInterrupt:
            logging.getLogger(__name__).warning(
                "Interrupted — CloudWorkerPool.__exit__ + terminate_fleet ran."
            )
            sys.exit(130)
    else:
        instance_config = InstanceConfig(
            game_dir=args.game_dir,
            num_instances=args.num_instances,
        )
        try:
            with LocalInstancePool(instance_config) as pool:
                study = optimize_hull(
                    args.hull, game_data, pool, opponent_pool, config, manifest,
                )
        except KeyboardInterrupt:
            logging.getLogger(__name__).warning(
                "Interrupted — LocalInstancePool.__exit__ ran teardown; if any "
                "JVMs/Xvfb remain, use `uv run python scripts/stop_optimizer.py`."
            )
            sys.exit(130)

    _print_results(study, args.hull, game_data)


def _print_results(study, hull_id: str, game_data=None):
    """Print top-10 builds from the study.

    Shows repaired builds when game_data is provided (accurate domain values),
    falls back to raw trial params otherwise (Baldwinian: pre-repair values).
    """
    from optuna.trial import TrialState
    from starsector_optimizer.optimizer import trial_params_to_build
    from starsector_optimizer.repair import repair_build

    all_trials = study.trials
    completed = [t for t in all_trials if t.state == TrialState.COMPLETE]
    pruned = [t for t in all_trials if t.state == TrialState.PRUNED]
    trials = sorted(completed, key=lambda t: t.value or 0, reverse=True)
    print(f"\n{'='*60}")
    print(f"Top 10 builds for {hull_id} ({len(completed)} completed, "
          f"{len(pruned)} pruned, {len(all_trials)} total)")
    print(f"{'='*60}")
    for i, trial in enumerate(trials[:10]):
        if game_data and hull_id in game_data.hulls:
            hull = game_data.hulls[hull_id]
            raw = trial_params_to_build(trial.params, hull_id)
            build = repair_build(raw, hull, game_data)
            weapons = {s: w for s, w in build.weapon_assignments.items() if w is not None}
            mods = sorted(build.hullmods)
            vents, caps = build.flux_vents, build.flux_capacitors
        else:
            weapons = {k: v for k, v in trial.params.items()
                       if k.startswith("weapon_") and v != "empty"}
            mods = sorted(k.removeprefix("hullmod_") for k, v in trial.params.items()
                          if k.startswith("hullmod_") and v is True)
            vents = trial.params.get("flux_vents", 0)
            caps = trial.params.get("flux_capacitors", 0)
        print(f"\n#{i+1} fitness={trial.value:.4f}")
        print(f"  Weapons: {', '.join(f'{k}={v}' for k, v in sorted(weapons.items()))}")
        print(f"  Hullmods: {', '.join(mods)}")
        print(f"  Vents={vents}, Caps={caps}")


if __name__ == "__main__":
    main()
