#!/usr/bin/env python3
"""Step 2: Validate optimizer with real combat simulation on Eagle.

Evaluates builds in batches to maximize parallel instance utilization.
With 8 instances and 2 opponents, evaluates 4 builds per batch (8 matchups).
"""

import sys
import time

sys.path.insert(0, "src")

from pathlib import Path

import optuna

from starsector_optimizer.parser import load_game_data
from starsector_optimizer.search_space import build_search_space
from starsector_optimizer.repair import repair_build
from starsector_optimizer.scorer import heuristic_score
from starsector_optimizer.variant import build_to_build_spec
from starsector_optimizer.instance_manager import InstanceConfig, InstancePool
from starsector_optimizer.opponent_pool import (
    OpponentPool, generate_matchups, hp_differential, get_opponents,
)
from starsector_optimizer.optimizer import (
    OptimizerConfig, BuildCache, define_distributions,
    build_to_trial_params, trial_params_to_build, warm_start,
)
from starsector_optimizer.models import HullSize

GAME_DIR = Path("game/starsector")
NUM_INSTANCES = 8
SIM_BUDGET = 24  # total builds to evaluate
BUILDS_PER_BATCH = 4  # 4 builds × 2 opponents = 8 matchups → 8 instances

# Reduced opponent pool (2 opponents for speed)
TEST_POOL = OpponentPool(pools={
    HullSize.CRUISER: ("dominator_Assault", "eagle_Assault"),
})

print("=" * 70, flush=True)
print(f"Eagle Optimization: {NUM_INSTANCES} instances, {SIM_BUDGET} builds, "
      f"{BUILDS_PER_BATCH} per batch", flush=True)
print("=" * 70, flush=True)

# Load
print("\n1. Loading game data...", flush=True)
gd = load_game_data(GAME_DIR)
hull = gd.hulls["eagle"]
from starsector_optimizer.models import REGIME_ENDGAME
space = build_search_space(hull, gd, REGIME_ENDGAME)
distributions = define_distributions(space)
opponents = get_opponents(TEST_POOL, HullSize.CRUISER)
print(f"   {len(distributions)}D space, {len(opponents)} opponents: {opponents}", flush=True)

# Setup instances
print(f"\n2. Setting up {NUM_INSTANCES} instances...", flush=True)
config = InstanceConfig(
    game_dir=GAME_DIR,
    num_instances=NUM_INSTANCES,
    xvfb_base_display=200,
)
pool = InstancePool(config)
pool.setup()

try:
    # Create study
    print("\n3. Creating Optuna study with warm-start...", flush=True)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sampler = optuna.samplers.TPESampler(
        multivariate=True, constant_liar=True,
        n_ei_candidates=256, n_startup_trials=20,
    )
    study = optuna.create_study(sampler=sampler, direction="maximize")
    opt_config = OptimizerConfig(warm_start_n=200, warm_start_sample_n=20000, warm_start_scale=0.1)
    warm_start(study, hull, gd, opt_config)
    print(f"   {len(study.trials)} warm-start trials", flush=True)

    # Batched optimization loop
    print(f"\n4. Running {SIM_BUDGET} builds in batches of {BUILDS_PER_BATCH}...", flush=True)
    cache = BuildCache()
    results_log = []
    best_fitness = -999
    t_start = time.monotonic()
    num_batches = SIM_BUDGET // BUILDS_PER_BATCH

    for batch_idx in range(num_batches):
        t_batch = time.monotonic()

        # Ask for BUILDS_PER_BATCH trials at once
        trials = []
        builds = []
        for _ in range(BUILDS_PER_BATCH):
            trial = study.ask(distributions)
            raw = trial_params_to_build(trial.params, "eagle")
            repaired = repair_build(raw, hull, gd)
            trials.append(trial)
            builds.append(repaired)

        # Generate all build specs and all matchups as one big batch
        all_matchups = []
        variant_ids = []
        for j, build in enumerate(builds):
            build_idx = batch_idx * BUILDS_PER_BATCH + j
            vid = f"eagle_val_{build_idx:03d}"
            build_spec = build_to_build_spec(build, hull, gd, vid)
            matchups = generate_matchups(build_spec, opponents, f"val_{build_idx:03d}",
                                         time_mult=5.0, time_limit_seconds=180.0)
            all_matchups.extend(matchups)
            variant_ids.append(vid)

        # Evaluate matchups across instances
        all_results = []
        for i, m in enumerate(all_matchups):
            all_results.append(pool.run_matchup(i % pool.num_instances, m))

        # Map results back to each build
        results_by_build = {vid: [] for vid in variant_ids}
        for r in all_results:
            # Find which build this result belongs to
            for vid in variant_ids:
                if r.matchup_id.startswith(vid.replace("eagle_val_", "val_")):
                    results_by_build[vid].append(r)
                    break

        # Tell results to study
        batch_elapsed = time.monotonic() - t_batch
        for j, (trial, build, vid) in enumerate(zip(trials, builds, variant_ids)):
            build_results = results_by_build[vid]
            if not build_results:
                study.tell(trial, -1.0)  # No results = worst
                continue

            from starsector_optimizer.combat_fitness import aggregate_combat_fitness
            fitness = aggregate_combat_fitness(build_results, mode="mean")
            cache.put(build, fitness)
            study.tell(trial, fitness)
            best_fitness = max(best_fitness, fitness)

            build_idx = batch_idx * BUILDS_PER_BATCH + j
            heur = heuristic_score(build, hull, gd).composite_score
            opp_details = []
            for r in build_results:
                opp = r.matchup_id.split("_vs_")[-1] if "_vs_" in r.matchup_id else "?"
                opp_details.append(f"{opp}:{r.winner[:1]}{hp_differential(r):+.2f}")

            n_w = sum(1 for v in build.weapon_assignments.values() if v is not None)
            print(f"   [{build_idx+1:>2}/{SIM_BUDGET}] fit={fitness:+.3f} best={best_fitness:+.3f} "
                  f"h={heur:.2f} w={n_w} [{', '.join(opp_details)}]", flush=True)

            results_log.append((build_idx, fitness, heur, build))

        print(f"   --- batch {batch_idx+1}/{num_batches} in {batch_elapsed:.0f}s ---", flush=True)

    total = time.monotonic() - t_start

    # Summary
    print(f"\n{'='*70}", flush=True)
    print("SUMMARY", flush=True)
    print(f"{'='*70}", flush=True)
    fitnesses = [f for _, f, _, _ in results_log]
    heurs = [h for _, _, h, _ in results_log]
    print(f"Total: {len(results_log)} builds in {total:.0f}s ({total/60:.1f}min)", flush=True)
    print(f"Per build: {total/len(results_log):.1f}s", flush=True)
    print(f"Fitness: [{min(fitnesses):+.3f}, {max(fitnesses):+.3f}], mean={sum(fitnesses)/len(fitnesses):+.3f}", flush=True)
    winners = sum(1 for f in fitnesses if f > 0)
    print(f"Winning builds: {winners}/{len(fitnesses)} ({winners/len(fitnesses)*100:.0f}%)", flush=True)

    if len(fitnesses) >= 5:
        from scipy.stats import spearmanr
        rho, p = spearmanr(heurs, fitnesses)
        print(f"Heuristic-sim correlation: rho={rho:.3f} (p={p:.3f})", flush=True)

    # Top-3
    results_log.sort(key=lambda x: -x[1])
    print(f"\nTop-3 builds:", flush=True)
    for rank, (idx, fit, heur, build) in enumerate(results_log[:3]):
        weapons = {s: w for s, w in build.weapon_assignments.items() if w is not None}
        logistics = [m for m in build.hullmods if gd.hullmods.get(m) and gd.hullmods[m].is_logistics]
        combat = [m for m in build.hullmods if m not in logistics]
        print(f"  #{rank+1} fitness={fit:+.3f} heuristic={heur:.3f}", flush=True)
        print(f"     Weapons ({len(weapons)}): {', '.join(sorted(weapons.values()))}", flush=True)
        print(f"     Combat: {', '.join(sorted(combat)) or 'none'}", flush=True)
        print(f"     Logistics: {', '.join(sorted(logistics)) or 'none'}", flush=True)
        print(f"     Vents={build.flux_vents}, Caps={build.flux_capacitors}", flush=True)

finally:
    print("\n5. Teardown...", flush=True)
    pool.teardown()
    print("   Done.", flush=True)
