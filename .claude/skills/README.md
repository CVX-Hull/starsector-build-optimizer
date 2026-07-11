# Skills — Index

Repo-local procedural skills: workflow gates (quality enforcement at fixed
points in the dev cycle) and SOPs (task-triggered how-tos). One line each;
open the skill before acting in its domain.

## Workflow gates (in dev-cycle order)

| Skill | Invoke when |
|---|---|
| [plan-lifecycle](plan-lifecycle.md) | Creating / updating / retiring plan files for non-trivial implementation. |
| [ddd-tdd](ddd-tdd.md) | Planning before non-trivial implementation, and during coding (step 3). |
| [plan-review](plan-review.md) | Reviewing a plan before implementation approval. |
| [post-impl-audit](post-impl-audit.md) | After all tasks in an implementation complete. |
| [design-invariants](design-invariants.md) | Reviewing any change — mechanical invariant checklist + grep commands. |
| [honest-evaluation](honest-evaluation.md) | After every major optimization run, before any report publishes findings. |
| [doc-grooming](doc-grooming.md) | After filing a report, retiring a plan, completing a wave, or changing skills — index/roadmap/skill hygiene + always-loaded budget. |

## SOPs (task-triggered)

| Skill | Invoke when |
|---|---|
| [cloud-worker-ops](cloud-worker-ops.md) | Any AWS work: campaigns, honest-eval sweeps, learned batches, AMI bake/cleanup, teardown, cost budgets. |
| [starsector-modding](starsector-modding.md) | Java mod work: sandbox, file I/O, Janino, combat plugin patterns, known game-fact pitfalls. |
| [empirical-report](empirical-report.md) | Writing or retrofitting any numerical report in `docs/reports/` (Methods-before-Results structure, tables, charts). |

Keep this table in sync when adding a skill; the gate table in `AGENTS.md`
mirrors the gate rows only.
