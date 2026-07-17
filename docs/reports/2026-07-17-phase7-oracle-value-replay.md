---
type: report
status: shipped
last-validated: 2026-07-17
---

# Phase 7 — Tier-2 oracle-value prequential replay (accounting stream)

## Abstract

Second prequential-replay reading (roadmap item 3; spec 31 §"Prequential
Replay Ablation"), now with the **Tier-2 oracle coverage** the shipped
wave-1 run lacked: 27 rank-stratified hammerhead builds re-scored against
the closed opponent pool by the transform-free honest evaluator (all 27 at
full 1,620/1,620 matchup coverage, zero failures). Run over 9 hammerhead
cells (seeds 100–108, gate scope) + 3 wolf cells (seeds 120–122,
directional) of the fresh accounting stream, locally, zero sim spend.

**Predeclared headline (Tier-2, oracle-value recovery under the CatBoost
opponent-adjusted arm, median over hammerhead cells): the oracle coverage
does _not_ certify the surrogate.** The CatBoost selection arm's
predicted-score-vs-oracle rank correlation is **+0.34 (p = 0.08, n = 27)
across the full rank-stratified set but ≈ 0 (+0.01, n = 13) among the
_rankable_ (finalized, deployable) builds** — the positive full-set signal
is carried almost entirely by the coarse pruned-bottom-vs-finalized-top
separation, and vanishes once restricted to the builds an optimizer would
actually deploy. Oracle values _are_ monotone across the three predicted
strata (bottom −0.319 < middle −0.262 < top −0.215) but with a realized
spread (0.10) far below the predicted spread (0.45): the surrogate
massively over-separates. The strongest positive oracle signal on this
stream is **not** the surrogate but the **TWFE α̂ estimator arm** (the
gating/honest-eval target): campaign-level Spearman **+0.50 to +0.58**
across arms (n = 13, 95% CIs marginal — A0 excludes zero, A1/A2/A3/EB do
not) — i.e. the coverage validates the _target_, not the model. The gating
statistic reproduces the wave-1 null exactly: CatBoost median q\* = 0.3 vs
the build-blind opponent-mean null = 0.3 on the 9 hammerhead cells. The T2
opponent-adjusted fidelity drift result reproduces: CatBoost carries the
**largest** positive signal on near-future proposals (+0.087 at 0–10,
collapsing to ≈ 0 beyond 20 trials; other build-aware arms are weakly
positive near-horizon, the build-blind null negative throughout). Net: the
Tier-2
coverage **confirmed** the shipped "surrogate gating value not established"
verdict against an independent oracle rather than overturning it. All
claims exploratory; no go/no-go is claimed. This report covers the
oracle-value question only — not the offline MCBO bake-off (the Phase 7
gate's second input) nor any novel-build / cross-hull generality (the
stream is TPE-interpolative by construction).

## Pre-registration fidelity (read first)

The Tier-2 statistic was predeclared (pre-registration entry 0/1,
`2026-07-14-accounting-stream-preregistration.md`) as "continuous
oracle-value regret@k under the CatBoost opponent-adjusted arm, aggregated
as the median over the hammerhead cells." That phrase does **not** map
verbatim to a single shipped-tool output, and honesty requires naming the
gap rather than papering over it:

- The spec-31 replay tool computes oracle **recovery** as (primary)
  within-replay-cell pairwise concordance and (secondary) campaign-level
  rank correlation, both over the **TWFE estimator arms** (A0–A3, EB) — not
  the CatBoost surrogate — plus a **Δ-oracle-value** accounting inside the
  CatBoost **gating** simulation (`oracle_skipped`). There is no native
  "oracle regret@k under CatBoost" scalar.
- This report realizes the predeclared statistic three faithful ways and
  reports **all** of them (they agree in direction, so the conclusion is
  robust to the mapping): (a) the **literal** reading — CatBoost
  selection-arm predicted score vs oracle build mean, Spearman, §5; (b) the
  **gating** reading — Δ-oracle value of oracle'd builds skipped by the
  CatBoost gating policy, §4; (c) the tool-native estimator-arm oracle
  recovery, §5, reported as the target-validation reading it actually is.
- The mapping was fixed by the spec-31 contract (which predates this
  stream), not chosen after the readings existed; the selector records the
  pre-registration commit (`prereg_commit = 424826a`) so "fixed before
  selection" is verifiable. The residual imprecision is in the
  pre-registration _wording_, not in post-hoc statistic selection. A
  spec-31 amendment folding an explicit "oracle regret under the gating
  arm" definition is filed as a follow-up so future pre-registrations name
  a tool output verbatim.

## Methods

### Data

Unit of analysis: one **replay cell** = (campaign, seed) study of the
frozen accounting matchup DB (`data/phase7/accounting_matchups.sqlite`;
`training_matchups` 21,969 rows, `recovered_builds` 5,751,
`honest_eval_matchups` 43,740). **9 hammerhead/early cells** (seeds
100–108) are the gate scope; **3 wolf/early cells** (seeds 120–122) are
accounting + directional replay only (cross-hull claims stay gated behind
the item-4 wave). Trials are arrival-ordered (eval-log timestamp,
tie-broken by trial_number); matchup rows join the eval logs on
`(source_path, trial_number)` with hard-error totality; pruned trials are
kept in training (4–9 realized rows; finalized have ~10). Hammerhead:
244–250 trials/cell (median 247, 2,225 total), 1,105 finalized (rankable)
trials (median 116/cell), 17,681 matchup rows; the incumbent pruner avoided
4,569 rows (**20.5%** of the counterfactual — roughly double the wave-1
10.0%, reflecting the more aggressive shipped pruner). Measured in-flight
gap Ĝ = **7–14 trials (median 8)**, below wave-1's median 12.

**Oracle panel** (`honest_eval_matchups`): the 27 rank-stratified builds
(3 predicted-rank strata × 1 build/stratum × 9 cells; pre-registration
entry 2 deterministic selector, `phase7_select_oracle_builds.py`), each
re-scored against a 54-variant closed opponent pool × 30 replicates = 1,620
matchups, **all at full coverage, zero failures** (honest-eval rule 3
satisfied). Provenance `build_id → build_key` resolved from the selector
JSON via the materializer's `--honest-selector-json` (0 unresolved). Wolf
cells receive **no** oracle coverage. Sources:
`data/logs/accounting-{hammerhead,wolf}/…/evaluation_log.jsonl` (12 logs),
`data/study_dbs/accounting-{hammerhead,wolf}/…` (in-flight measurement
only), `data/honest_eval/starsector-honest-eval-accounting-hammerhead-20260716T141904Z/results.jsonl`.

**Rankability skew (load-bearing).** Only **13 of 27** oracle'd builds are
rankable (finalized) in the replay; the other 14 are pruned trials, absent
from the arm rankings. The rankable set is skewed to the top stratum
(bottom 1, middle 4, top 8) because the pruner finalizes high-predicted
builds and prunes low-predicted ones — itself weak corroboration that the
surrogate and TPE agree on which builds are worth finalizing, but it
means the fine-grained rank-recovery statistics run at n = 13 over a
top-heavy support, not n = 27.

### Estimators / models

Identical to the shipped run (spec 31; same code, `code_version`
`7be10b1`): surrogate arms `catboost_regressor` +
`random_forest_tuned` at `DEFAULT_HYPERPARAMETERS` (no per-cutoff HPO,
predeclared); comparator-gate families incl. the mandatory build-blind
`opponent_mean` null; TWFE estimator arms A0/A1/A2/A3/EB (math owned by
spec 28 / `deconfounding.py`), on the `hp_differential` target. The
**selection** arm (which chose the 27) is `catboost_regressor`
opponent-adjusted, fit **in-sample per cell** on all of that cell's
matchup rows, used only to stratify — oracle values are measured
independently, so overfit cannot leak into the oracle-value statistic
(pre-registration entry 2 risk note).

### Statistical-learning setup

Unchanged from the shipped run: prequential cutoffs at trial 40, stride 10,
while ≥ 10 future trials remain; training = prefix minus the in-flight gap
G; scoring bucketed by distance (0–10 / 10–20 / 20–40 / tail); feature
schema v4 profile `all`; decision-time planned-panel scoring; T1
finalized-only; honest-eval targets are post-fit evaluation targets only
(`honest_eval_usage = exploratory_selection`, `claim_label = exploratory`);
`hpo_seed` 23, bootstrap/tie-break seed 331; single-threaded RF prediction;
`PYTHONHASHSEED=0` pinned for artifact reproducibility (see Appendix). No
model-selection criterion — the headline statistic and sensitivity labels
were predeclared before the sweep.

### Comparison statistics

Fidelity: cell-mean Spearman per (cell, cutoff, bucket, arm), reported as
hammerhead-cell means (n = 9; wolf n = 3 directional). Gating: per-cell
q\* = max q ∈ {0.1, 0.2, 0.3, 0.5} with zero realized top-3 regret under
the A1 target; headline = median q\* over the 9 hammerhead cells,
CatBoost, G = Ĝ, with the `opponent_mean` null alongside. Oracle recovery:
(literal) CatBoost predicted-score vs oracle build-mean Spearman;
(gating) Δ-oracle value of oracle'd builds skipped by the CatBoost policy;
(tool-native) within-cell pairwise concordance + campaign-level Spearman of
estimator arms vs oracle means, cell/build bootstrap CIs.

### Diagnostics & thresholds

Predeclared caveats (spec 31, verbatim): the replay measures filtering
fidelity on the logged stream and cannot measure the counterfactual TPE
trajectory had proposals actually been skipped; the stream is
forward-deployment evidence over later proposals of the _same_ studies, not
novel-build or cross-hull evidence. No pass/fail threshold is attached — the
reading is one of the two predeclared inputs to the Phase 7 BoTorch go/no-go
(the other being the MCBO bake-off).

## Results

### 1. Stream characterization

9 hammerhead cells, 244–250 trials each (median 247); Ĝ 7–14 (median 8) —
at any completion roughly a dispatch block was in flight, so the zero-gap
trainer is optimistic by ~8 trials. The pruner avoided 4,569 rows (20.5% of
the 22,250-row counterfactual), the gating-savings reference. 13 of 27
oracle'd builds are rankable (top-heavy; §Data).

### 2. Panel-matched fidelity (T1) by horizon

**Statistic: hammerhead cell-mean Spearman, G = Ĝ.**

| Arm | 0–10 | 10–20 | 20–40 | tail |
|---|---:|---:|---:|---:|
| catboost_regressor | 0.447 | 0.421 | 0.427 | 0.382 |
| random_forest_tuned | 0.411 | 0.384 | 0.416 | 0.379 |
| opponent_mean (null) | 0.369 | 0.421 | 0.411 | 0.372 |

**Reading.** As in wave-1, T1 is dominated by opponent-panel composition:
the build-blind null posts 0.37–0.42 with zero build knowledge, and
CatBoost's edge over the null is small (≈ 0.08 ρ at the adjacent block) and
gone by the 10–20 bucket. T1 does not decay with distance. Most of T1 is
the C1 confound reproduced prequentially — not deployable build signal.

### 3. Opponent-adjusted fidelity (T2) — drift reproduces

**Statistic: hammerhead cell-mean Spearman vs full-data A1 α̂, G = Ĝ.**

| Arm | 0–10 | 10–20 | 20–40 | tail |
|---|---:|---:|---:|---:|
| catboost_regressor | **0.087** | 0.055 | −0.001 | −0.031 |
| random_forest_tuned | 0.004 | −0.034 | −0.093 | −0.102 |
| opponent_mean (null) | −0.065 | −0.044 | −0.092 | −0.121 |

**Reading.** The wave-1 drift result reproduces on an independent stream:
once opponent effects are removed, CatBoost carries the **largest** positive
rank signal on future proposals (ρ ≈ 0.09 within 10 trials ahead) and
**collapses to ≈ 0 beyond 20 trials**. Other build-aware arms are weakly
positive near-horizon (of the arms not shown, `ridge_hybrid` ≈ +0.070,
`random_forest` ≈ +0.055 at 0–10) and collapse similarly; the build-blind
`opponent_mean` null and `twfe_additive` are **negative** throughout — the
5C-curriculum signature (later, better builds faced harder panels, so panel
difficulty anti-correlates with build quality along the stream). The
near-horizon CatBoost signal (0.087) is smaller than wave-1's adjacent-block
0.122; the qualitative shape (CatBoost-largest, near-horizon, tail-collapse,
negative build-blind null) is identical.

**Wolf (directional, n = 3).** CatBoost T2 is _stronger_ and more
persistent than hammerhead: **+0.261 / +0.281 / +0.103 / +0.018** across
buckets, with the null strongly negative (−0.199 → −0.446). On the
non-meta frigate the surrogate's opponent-adjusted signal is the clearest
in the program — but n = 3 cells, no oracle coverage, directional only.

### 4. Gating simulation — reproduces the null

**Statistic: per-cell q\*; headline = median over 9 hammerhead cells,
CatBoost, G = Ĝ, A1 target, top-3.**

| Quantity | CatBoost | opponent_mean null |
|---|---:|---:|
| median q\* (hammerhead) | **0.3** | 0.3 |
| cells with q\* = 0 | 3/9 | 3/9 |
| rows saved at q = 0.3 | 21.2% | — |

Per-cell surrogate q\*: {100: 0.5, 101: 0.3, 102: 0.0, 103: 0.5, 104: 0.3,
105: 0.5, 106: 0.0, 107: 0.5, 108: 0.0}; null: {100: 0.3, 101: 0.0,
102: 0.0, 103: 0.2, 104: 0.3, 105: 0.3, 106: 0.3, 107: 0.5, 108: 0.0}.

**Δ-oracle under the CatBoost gating policy (the pre-registered "regret"
reading).** At the headline q = 0.3 the CatBoost policy skips only **2 of
13** rankable oracle'd builds (oracle values −0.223, −0.125); at q = 0.1
just 1 (−0.125). The skips are mildly unfavorable — the −0.125 build is
above the top-stratum oracle mean (−0.215), so the policy does skip one
genuinely good build — but the Δ-oracle magnitude is small and the policy
keeps 11/13 oracle'd builds at q = 0.3.

**Reading.** The headline does not separate the surrogate gate from the
build-blind null at the median (0.3 vs 0.3), reproducing wave-1 — with
~10-trial blocks and top-3 zero-regret as the bar, panel-difficulty
knowledge alone safely skips 30% in the median cell. As in wave-1, the null
ranks _inversely_ to build quality (§3), so its parity here is exactly the
point: this statistic, on this stream, cannot certify the surrogate's
gating value even with deeper per-cell streams (247 vs wave-1's ~150 trials)
and oracle coverage.

### 5. Oracle-value recovery — the Tier-2 payoff, three ways

**(a) Literal — CatBoost predicted score vs oracle build mean (Spearman).**

| Support | n | Spearman | p |
|---|---:|---:|---:|
| all rank-stratified builds | 27 | **+0.343** | 0.080 |
| rankable (finalized) builds only | 13 | **+0.011** | 0.972 |

Per-stratum means: predicted −0.524 / −0.295 / −0.079 (bottom/middle/top,
monotone by construction); oracle **−0.319 / −0.262 / −0.215** (monotone,
bottom < middle < top). The oracle confirms the coarse tertile ordering,
but the realized spread (0.10) is **far below** the predicted spread (0.45)
— the surrogate over-separates by ~4×. Restricted to deployable (rankable)
builds the correlation is **null** (+0.01): the +0.34 full-set signal is
carried entirely by the coarse separation of pruned-bottom from finalized
builds, not by fine ranking among the builds that would actually deploy.

**(b) Gating Δ-oracle:** §4 — CatBoost keeps 11/13 rankable oracle'd builds
at q = 0.3; small, mildly unfavorable regret.

**(c) Tool-native estimator-arm recovery.** Campaign-level Spearman of the
TWFE α̂ arms vs oracle means (n = 13 rankable, common opponents 52):

| Arm | Spearman | 95% CI |
|---|---:|---|
| A0 | 0.577 | [0.071, 0.854] |
| A2 | 0.522 | [−0.008, 0.849] |
| A1 | 0.500 | [−0.020, 0.835] |
| EB | 0.500 | [−0.034, 0.802] |
| A3 | 0.489 | [−0.059, 0.833] |

Within-cell pairwise concordance (6 pairs, direction check only): A0/A2/A3/EB
0.50, A1 0.33 — indiscriminable, as predeclared.

**Reading.** The one clearly positive oracle signal on this stream is the
**TWFE α̂ estimator** (A0 ≈ 0.58, only A0's CI excludes zero), **not** the
CatBoost surrogate (literal reading ≈ 0 on rankable builds). Since the α̂
arms are the gating target and the honest-eval ranking basis, this
**validates the target** — the opponent-adjusted α̂ tracks the transform-free
oracle at +0.5 — while leaving the **surrogate's** deployable-build ranking
uncertified. The apparent contradiction with the estimator arms is not one:
A0–EB are full-data TWFE estimators fit on the stream, whereas the CatBoost
selection score is a per-cell in-sample stratifier; they measure different
things, and only the former recovers oracle rank among rankable builds.

## Synthesis & decisions

1. **Roadmap item 3 is delivered**: the instrumented accounting stream
   exists, is materialized with oracle coverage into a retained frozen DB,
   and its Tier-2 replay reading is in. The matchups-per-trial accounting is
   filed separately ([2026-07-17 accounting spread](2026-07-17-accounting-matchup-spread.md)).
2. **The Tier-2 oracle coverage did _not_ certify the surrogate — it
   confirmed the shipped "not established" verdict against an independent
   oracle.** The CatBoost surrogate recovers only the coarse
   good-vs-clearly-bad separation (n = 27 Spearman +0.34, marginal;
   dominated by the pruned bottom stratum) and is **null on the
   deployable-build ranking** (n = 13 Spearman +0.01). Its gating value is
   not separable from the build-blind null (q\* 0.3 vs 0.3). The
   opponent-adjusted T2 drift reproduces. This is the deployment-side
   counterpart of the adversarial-AUC interpolation finding, now with oracle
   confirmation: **the ceiling on this stream is the data (TPE-interpolative,
   pruning-skewed rankable set), not the estimator** — strengthening the
   data-first ordering. No go/no-go is claimed.
3. **What the coverage _did_ establish (positive):** the opponent-adjusted
   **α̂ target** tracks the transform-free oracle at +0.5 (marginal), so the
   gating/honest-eval target is oracle-valid; and the oracle tertile
   ordering is monotone, so the surrogate is not anti-informative — it
   under-resolves, it does not invert.
4. **For the Phase 7 BoTorch gate**: this reading argues the designed data
   wave (item 4: balanced opponent panels + off-TPE build arm) is the lever
   that could raise deployable-build oracle recovery above the ≈ 0 measured
   here; the MCBO bake-off remains the gate's other input.

## Open questions / next steps

- Can the designed data wave (item 4) lift the **rankable-build** CatBoost
  vs oracle Spearman off ≈ 0? That n = 13, +0.01 is the pre-wave baseline.
  The paired augmented-vs-unaugmented replay on this stream's own cells
  (re-groom 2026-07-14) is the comparison of record.
- Would a larger oracle-coverage K (or coverage of pruned-stratum builds,
  which are currently unrankable) power the fine-grained oracle recovery
  the n = 13 top-heavy support cannot?
- The spec-31 amendment naming an explicit gating-arm oracle-regret statistic
  (Pre-registration fidelity §) so future pre-registrations bind to a tool
  output verbatim.
- The MCBO bake-off remains the unaddressed half of the BoTorch go/no-go.

## Appendix — file map

- Producer: `scripts/analysis/phase7_prequential_replay.py` (spec 31);
  selector `scripts/analysis/phase7_select_oracle_builds.py`; materializer
  `scripts/analysis/phase7_materialize_matchups.py`; accounting
  `scripts/analysis/accounting_extract.py`.
- Raw artifacts (gitignored `data/`, in the retained-paths manifest):
  frozen DB `data/phase7/accounting_matchups.sqlite` (re-materialized
  2026-07-16 with `--honest-ledger` + `--honest-selector-json` under pinned
  `PYTHONHASHSEED=0`; `training_matchups` byte-identical to the pre-oracle
  materialization — verified checksum — so stream comparability holds);
  replay output `data/phase7/accounting_replay.json`; selector
  `data/phase7/accounting_oracle_builds.json` (`prereg_commit = 424826a`);
  honest-eval ledger under
  `data/honest_eval/starsector-honest-eval-accounting-hammerhead-20260716T141904Z/`.
- **Reproducibility caveat**: study-DB build _reconstruction_ (unreferenced
  lookup builds only) is `PYTHONHASHSEED`-dependent (4076 distinct at seed 0
  vs 4101 at seed 12345); `training_matchups`, every referenced build_key,
  and all replay/oracle statistics are unaffected (build_key is
  content-addressed). The frozen DB is pinned to seed 0; a follow-up fixes
  the reconstruction determinism so the pin can be dropped.
- Pre-registration + ledger:
  [2026-07-14 accounting-stream pre-registration](2026-07-14-accounting-stream-preregistration.md)
  (entry 3 records this reading). Prior stream:
  [2026-07-14 wave-1 prequential replay](2026-07-14-phase7-prequential-replay.md).
  Owning plan: `2026-07-14-instrumented-accounting-run.md`.
