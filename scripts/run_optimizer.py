#!/usr/bin/env python3
"""Run ship build optimization for a single hull."""

import argparse
import sys

sys.path.insert(0, "src")

from pathlib import Path

from starsector_optimizer.parser import load_game_data
from starsector_optimizer.instance_manager import InstanceConfig, InstancePool
from starsector_optimizer.curtailment import CurtailmentMonitor
from starsector_optimizer.opponent_pool import DEFAULT_OPPONENT_POOL
from starsector_optimizer.optimizer import OptimizerConfig, optimize_hull


def main():
    parser = argparse.ArgumentParser(description="Optimize a ship build")
    parser.add_argument("--hull", required=True, help="Hull ID to optimize (e.g. wolf, eagle)")
    parser.add_argument("--game-dir", required=True, type=Path, help="Path to Starsector installation")
    parser.add_argument("--num-instances", type=int, default=2, help="Parallel game instances")
    parser.add_argument("--sim-budget", type=int, default=50, help="Number of build evaluations")
    parser.add_argument("--study-db", type=str, default=None, help="SQLite path for study persistence")
    parser.add_argument("--fitness-mode", choices=["mean", "minimax"], default="mean")
    parser.add_argument("--heuristic-only", action="store_true",
                        help="Use heuristic score instead of simulation (for testing)")
    args = parser.parse_args()

    print(f"Loading game data from {args.game_dir}...")
    game_data = load_game_data(args.game_dir)
    print(f"Loaded {len(game_data.hulls)} hulls, {len(game_data.weapons)} weapons")

    if args.hull not in game_data.hulls:
        print(f"Error: hull '{args.hull}' not found. Available: {sorted(game_data.hulls.keys())[:10]}...")
        sys.exit(1)

    storage = f"sqlite:///{args.study_db}" if args.study_db else None
    config = OptimizerConfig(
        sim_budget=args.sim_budget,
        fitness_mode=args.fitness_mode,
        study_storage=storage,
    )

    if args.heuristic_only:
        print("Heuristic-only mode: warm-start only, no simulation.")
        import optuna
        from starsector_optimizer.optimizer import warm_start
        study = optuna.create_study(direction="maximize", storage=storage,
                                     study_name=args.hull, load_if_exists=True)
        warm_start(study, game_data.hulls[args.hull], game_data, config)
        _print_results(study, args.hull)
        return

    instance_config = InstanceConfig(
        game_dir=args.game_dir,
        num_instances=args.num_instances,
    )
    curtailment = CurtailmentMonitor()

    with InstancePool(instance_config, curtailment=curtailment) as pool:
        pool.setup()
        study = optimize_hull(
            args.hull, game_data, pool, DEFAULT_OPPONENT_POOL, config,
            eval_log_path=Path("data/evaluation_log.jsonl"),
        )

    _print_results(study, args.hull)


def _print_results(study, hull_id: str):
    """Print top-10 builds from the study."""
    trials = sorted(study.trials, key=lambda t: t.value or 0, reverse=True)
    print(f"\n{'='*60}")
    print(f"Top 10 builds for {hull_id} ({len(study.trials)} total trials)")
    print(f"{'='*60}")
    for i, trial in enumerate(trials[:10]):
        weapons = {k: v for k, v in trial.params.items()
                   if k.startswith("weapon_") and v != "empty"}
        mods = [k.removeprefix("hullmod_") for k, v in trial.params.items()
                if k.startswith("hullmod_") and v is True]
        print(f"\n#{i+1} fitness={trial.value:.4f}")
        print(f"  Weapons: {', '.join(f'{k}={v}' for k, v in sorted(weapons.items()))}")
        print(f"  Hullmods: {', '.join(sorted(mods))}")
        print(f"  Vents={trial.params.get('flux_vents', 0)}, Caps={trial.params.get('flux_capacitors', 0)}")


if __name__ == "__main__":
    main()
