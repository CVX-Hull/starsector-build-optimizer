---
type: reference
status: shipped
last-validated: 2026-05-10
---

# Honest evaluation — methodology

After every major optimization run (Wave / production / large ablation campaign),
the run's top builds are re-scored against the **complete vanilla opponent
population** with high replication using a **transform-free oracle scorer**,
before any report publishes its findings. This is the closed-system analog
of an ML test set, and the only honest way to compare cells / runs whose
own training scorers used different transforms.

This doc is the design rationale. The contract lives in
[../specs/30-honest-evaluator.md](../specs/30-honest-evaluator.md). The
operational SOP — when to invoke, how to read the output, how to handle
mismatches against the within-cell ranking — lives in
[../../.claude/skills/honest-evaluation.md](../../.claude/skills/honest-evaluation.md).

## The problem

Each ablation cell uses a different scoring transform stack. C0a uses plain
TWFE α̂ (no shrinkage, no Box-Cox). C2 adds EB shrinkage + Box-Cox + sigmoid
shaping. The Optuna study under each cell ranks trials by *that cell's* shaped
score. So one cell's shaped `best_value` and another cell's shaped
`best_value` are **not on the same scale** — they're scores under different metrics. Picking the cell
with the highest `best_value` would be circular: it picks the cell whose
shaper happens to assign higher numbers, not the cell whose underlying builds
are actually stronger.

For ablation to mean anything, the cells' *outputs* (top builds) must be
judged by *the same scorer*, applied independently of which transform stack
each cell trained under.

## What "honest" means here

> Evaluate top builds from each cell against the same fixed external
> scorer applied to every variant in the closed opponent population, with
> enough replication that within-build noise is below the between-cell
> signal we're trying to detect.

Three independent constraints:

1. **Same external scorer across cells.** Any transform that's a function
   of the cell's training history (EB precision-weighting, Box-Cox lambda
   fit, sigmoid percentile) gets dropped. The oracle is balanced-design
   mean fitness — pure, transform-free.

2. **Closed opponent population.** Starsector's stock variants in
   `game/starsector/data/variants/` are an enumerable, finite set. The
   "test set" is not a sample, it IS the population. There is no held-out
   design problem and no generalization-to-unseen-distributions worry.

3. **High enough replication that the answer doesn't move.** Per-build
   standard error must be small enough that the cell ranking is stable —
   see "Replication count" below.

## Why mean fitness is the oracle (not TWFE)

In the optimizer's training loop, TWFE-A0 is a deconfounding transform
because the matchup matrix is **unbalanced** — different builds fight
different active-opponent subsets, and opponent quality varies. TWFE
decomposes `score_ij = α_i + β_j + ε_ij` to separate build quality from
opponent quality.

In honest evaluation, the design is **completely balanced**: every
evaluated build fights every variant in the population the same number
of times. Under a balanced design with uniform per-cell counts, the TWFE
fixed point degenerates to:

```
α̂_i  =  (row mean of build i)  −  (grand mean over all builds)
β̂_j  =  (column mean of opp j)  −  (grand mean)
```

(rank-equivalent — TWFE's ridge regularization adds a uniform multiplicative
shrink toward zero that doesn't affect rank). The β̂'s are mathematically
guaranteed to capture all opponent-difficulty effects exactly because every
build sees them in equal measure. So **deconfounding adds nothing** in this
regime; the simpler statistic is identical in rank.

Therefore the honest oracle is just:

```
oracle_score(build) = mean_{(opp, rep) in pool × replicates} combat_fitness(matchup(build, opp, rep))
```

Pure mean of `combat_fitness` (spec 25) over all matchups the build
participated in. No deconfounding, no shrinkage, no shaping.

## Why uniform weighting (not faction encounter frequency)

Starsector ships fight in faction-organized fleets. `data/world/factions/*.faction`
defines fleet composition rules, so we could weight oracle scores by encounter
frequency: "build is good if it wins against opponents you actually meet often."

We deliberately don't. Two reasons:

1. **The player's faction-targeting choice is unknowable a priori.** A
   pirate-bounty player will fight only Pirate fleets. A Tri-Tachyon
   employee will fight other corporates. A peaceful trader will fight
   nobody. Encoding any one weighting prejudges what the user is doing
   with the build.
2. **Uniform weighting is the player-faction-agnostic question.** "How
   does this build perform against the variants it might encounter,
   averaged over no specific faction" is a clean, single-number answer
   that doesn't bake in playstyle assumptions.

If a future use case demands faction-weighted scoring, it can be added as
a second oracle (uniform stays as the default). Documented as a deferred
feature 2026-05-10 per explicit user direction; do not implement without
reopening the question.

## Replication count — designed bound

`combat_fitness` is a hierarchical scalar bounded in `[-2, +1.5]` by spec 25
(no_engagement floor through PLAYER-win-at-full-hull ceiling). Per-matchup
variance is bounded above by `(1.5 − (−2))² / 4 = 3.0625` (Popoviciu's
upper bound for a bounded random variable), so `σ_per_matchup ≤ 1.75`. The
worst-case design bound for the standard error of a build's mean over
`M` opponents × `N` replicates is:

```
SEM_max(build) = 1.75 / sqrt(M × N)
```

The spec default `N = replicates_per_matchup = 30` is chosen so that for
hulls with `M ≥ 50` compatible opponents, `SEM_max ≤ 0.045` regardless of
the realised distribution shape. That places the worst-case 2-sigma
resolution between cells at ~0.09 score-units — adequate to distinguish
ablation cells whose mechanism delivers a meaningful effect (typically
≥ 0.10 oracle delta).

For hulls with smaller compatible pools (some CAPITAL ships fall below
`M = 20`), `N` should be raised to keep `SEM_max` in the same range:
`N = ceil(1.75² × M⁻¹ × SEM_target⁻²)`. The default is hull-agnostic;
operators set it explicitly via `--replicates` when running on a smaller
pool.

Realised SEM (`HonestEvaluationResult.evaluated_builds[i].oracle_se`)
is reported per build and is typically lower than the worst-case bound
(real distributions are not at Popoviciu's upper limit). Operators read
the realised SEM in the JSON output to confirm the resolution actually
achieved on each run.

## Closed-system framing — what's IN the test set, what isn't

`get_opponents(opponent_pool, hull.hull_size)` returns all stock variants
of *the same hull-size* as the player. That's the operational definition
of "compatible opponent" and matches what the optimizer trained against
(modulo `active_opponents` curriculum subsetting). Honest evaluation uses
the same `get_opponents` call without the `active_opponents` cap.

Explicitly NOT in the test set:

- **Cross-size matchups.** Hammerhead-vs-frigate, hammerhead-vs-cruiser.
  The optimizer never trains on these and we have no game-mechanic
  justification for adding them in evaluation; would smuggle a new
  game-rule decision into the methodology.
- **Modded ship variants.** The manifest-as-oracle invariant means we
  trust only what `ManifestDumper` exported from a vanilla install. Modded
  variants are out of scope by the same invariant; honest evaluation
  doesn't relax it.

## When to run honest evaluation

Standing rule: **after every major optimization run, before any report
publishes its findings**. "Major" means anything where the run's results
inform a decision (Wave / production / large ablation). One-off smoke
tests don't need it.

The skill at [../../.claude/skills/honest-evaluation.md](../../.claude/skills/honest-evaluation.md)
is the operational SOP — how to invoke, what cost to expect, how to
write up the result.

## What this is NOT

- **Not a statistical hypothesis test.** The output is per-cell mean
  oracle score + SEM. Operators can eyeball overlap. If a future ablation
  has cells too close to call from SEMs alone, formal hypothesis testing
  can be added then.
- **Not faction-weighted.** Deferred 2026-05-10 per user direction.
- **Not for modded content.** Manifest-as-oracle invariant.
- **Not for heuristic-only runs.** The oracle requires real combat sims;
  heuristic eval would defeat the purpose.
