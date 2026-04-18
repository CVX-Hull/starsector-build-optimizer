#!/usr/bin/env python3
"""Run ship build optimization for a single hull."""

import argparse
import logging
import signal
import sys

sys.path.insert(0, "src")

from pathlib import Path

from starsector_optimizer.parser import load_game_data
from starsector_optimizer.instance_manager import InstanceConfig, InstancePool
from starsector_optimizer.opponent_pool import discover_opponent_pool, get_opponents
from starsector_optimizer.optimizer import OptimizerConfig, optimize_hull


def _install_signal_handlers() -> None:
    """Route SIGTERM/SIGHUP to KeyboardInterrupt so `with InstancePool(...)`
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
    parser.add_argument("--sampler", choices=["tpe", "catcma"], default="tpe",
                        help="Optimization sampler: tpe (default) or catcma")
    parser.add_argument("--heuristic-only", action="store_true",
                        help="Use heuristic score instead of simulation (for testing)")
    parser.add_argument("--analyze-importance", action="store_true",
                        help="Run fANOVA importance analysis on existing study and exit")
    parser.add_argument("--active-opponents", type=int, default=10,
                        help="Max opponents per build (default 10, selects top-K from pool)")
    parser.add_argument("--fix-params", type=Path, default=None,
                        help="JSON file mapping param names to fixed values")
    args = parser.parse_args()

    _install_signal_handlers()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print(f"Loading game data from {args.game_dir}...")
    game_data = load_game_data(args.game_dir)
    print(f"Loaded {len(game_data.hulls)} hulls, {len(game_data.weapons)} weapons")

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
        study = optuna.load_study(study_name=args.hull, storage=storage)
        result = analyze_importance(study)
        print(print_importance_report(result))
        return

    config = OptimizerConfig(
        sim_budget=args.sim_budget,
        sampler=args.sampler,
        active_opponents=args.active_opponents,
        fixed_params=fixed_params,
        study_storage=storage,
        eval_log_path=Path("data/evaluation_log.jsonl"),
    )

    if args.heuristic_only:
        print("Heuristic-only mode: warm-start only, no simulation.")
        import optuna
        from starsector_optimizer.optimizer import warm_start
        study = optuna.create_study(direction="maximize", storage=storage,
                                     study_name=args.hull, load_if_exists=True)
        warm_start(study, hull, game_data, config)
        _print_results(study, args.hull, game_data)
        return

    instance_config = InstanceConfig(
        game_dir=args.game_dir,
        num_instances=args.num_instances,
    )
    try:
        with InstancePool(instance_config) as pool:
            pool.setup()
            study = optimize_hull(
                args.hull, game_data, pool, opponent_pool, config,
            )
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning(
            "Interrupted — InstancePool.__exit__ ran teardown; if any JVMs/Xvfb "
            "remain, use `uv run python scripts/stop_optimizer.py`."
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
