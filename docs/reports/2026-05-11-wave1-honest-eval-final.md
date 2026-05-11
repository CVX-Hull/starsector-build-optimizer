---
type: report
status: shipped
last-validated: 2026-05-11
---

# Wave 1 Honest-Eval Final Report

## Abstract

The resumed Wave 1 honest-eval run completed on 2026-05-11 and wrote final
per-cell outputs for `wave1-c0a`, `wave1-c0b`, `wave1-c1`, `wave1-c2`,
`wave1-c3`, and `random-baseline`. The final resource audits were clean: zero
tagged AWS instances remained in all audited regions after shutdown.

The main result is that the production candidate stack tested as `wave1-c2`
does not beat either baseline. By mean top-K oracle, the final ranking is
`c0a > c0b > c1 > c2 > c3 > random-baseline`. By top-1 oracle, `c1` produced
the strongest individual build, but its cell mean is weaker than c0a and c0b.
All five optimizer cells beat the random-feasible baseline by mean top-K
oracle.

## 1. Methods

Inputs:

- Summary: `data/campaigns/honest_eval_summary_2026-05-11.json`
- Per-cell outputs: `data/campaigns/{wave1-c0a,wave1-c0b,wave1-c1,wave1-c2,wave1-c3,random-baseline}/honest_eval.json`
- Ledger: `data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`
- Fixed-resume log: `data/honest_eval/orchestrator-20260511T032626Z.log`

Each cell evaluated 9 builds. Each build panel contains 54 opponents x 30
replicates = 1,620 matchups. Mean top-K oracle is the mean oracle score over
the 9 evaluated builds in a cell. Top-1 oracle is the best individual build in
that cell.

## 2. Final Results

| Cell | Builds | Mean top-K oracle | Top-1 oracle | Top-1 SE |
|---|---:|---:|---:|---:|
| wave1-c0a | 9 | -0.0906 | +0.1104 | 0.0250 |
| wave1-c0b | 9 | -0.1042 | +0.0610 | 0.0248 |
| wave1-c1 | 9 | -0.1131 | +0.2433 | 0.0245 |
| wave1-c2 | 9 | -0.1413 | +0.0302 | 0.0282 |
| wave1-c3 | 9 | -0.1417 | -0.0370 | 0.0241 |
| random-baseline | 9 | -0.2571 | +0.1151 | 0.0252 |

Cell ranking by mean top-K oracle:

1. `wave1-c0a`
2. `wave1-c0b`
3. `wave1-c1`
4. `wave1-c2`
5. `wave1-c3`
6. `random-baseline`

Cell ranking by top-1 oracle:

1. `wave1-c1`: +0.2433
2. `random-baseline`: +0.1151
3. `wave1-c0a`: +0.1104
4. `wave1-c0b`: +0.0610
5. `wave1-c2`: +0.0302
6. `wave1-c3`: -0.0370

## 3. Gate Read

F1c production-stack gate:

- C2 vs C0a: -0.0508
- C2 vs C0b: -0.0372

Point-estimate verdict: `wave1-c2` loses to both baselines. Do not promote
EB+Box-Cox as tested.

Random-baseline existence check:

- Random-baseline mean top-K oracle: -0.2571
- Optimization cells beating random-baseline mean: 5 / 5

The optimizer is extracting signal beyond random feasible sampling, but the
best default from this run is not c2.

Warm-start read:

- `wave1-c3` mean top-K oracle is -0.1417, slightly below c2 and far below
  c0a/c0b.
- `wave1-c3` top-1 oracle is -0.0370, below every other optimizer cell.

Keep warm-start quarantined. Any future warm-start work needs a focused
ablation rather than adoption from Wave 1.

## 4. Operational Audit

The fixed resume completed all 54 build panels and wrote:

- `data/campaigns/wave1-c0a/honest_eval.json`
- `data/campaigns/wave1-c0b/honest_eval.json`
- `data/campaigns/wave1-c1/honest_eval.json`
- `data/campaigns/wave1-c2/honest_eval.json`
- `data/campaigns/wave1-c3/honest_eval.json`
- `data/campaigns/random-baseline/honest_eval.json`
- `data/campaigns/honest_eval_summary_2026-05-11.json`

The wrapper final audit and watchdog final audit both reported zero tagged AWS
instances in `us-east-1`, `us-east-2`, `us-west-1`, and `us-west-2`.

The final log includes a small number of late timeout retries and discarded
loadout-mismatch attempts in the random-baseline tail. Those retries completed
successfully and did not prevent final output generation.

## 5. Decisions

- Do not promote c2 / EB+Box-Cox as the production default from Wave 1.
- Treat c0a as the best cell by mean top-K oracle.
- Treat c1 as the high-ceiling branch because it found the best individual
  build, but test repeatability before adopting it.
- Keep c3 warm-start out of the default path.
- Proceed with Phase 7 feature-substrate work using the completed honest-eval
  ledger and per-cell outputs.

## Appendix A. File Map

- Final report producer:
  `scripts/analysis/wave1_honest_eval_report.py`
- Digest producer:
  `scripts/honest_eval_digest.py`
- Summary JSON:
  `data/campaigns/honest_eval_summary_2026-05-11.json`
- Digest JSON:
  `data/honest_eval_digest.json`
