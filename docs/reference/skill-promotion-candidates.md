---
type: reference
status: shipped
last-validated: 2026-05-10
---

# Skill Promotion Candidates

This ledger records which repo-local workflows are portable enough to package
as general skills, and which details must remain local to this repository.

Use the general-applicability rule in [../CONVENTIONS.md](../CONVENTIONS.md):
portable skills should avoid repo-specific paths, tool-specific product names,
and historical implementation details unless the exact path or command is the
thing being documented.

## Promote

| Source workflow | Target package | Status | Portable skill shape | Keep local |
|---|---|---|---|---|
| `.claude/skills/ddd-tdd.md` | `spec-test-implementation-workflow/SKILL.md` | candidate | Spec-first, test-first implementation workflow. | `docs/specs/`, project test commands, root workflow invariants. |
| `.claude/skills/plan-review.md` | `plan-review/SKILL.md` | candidate | Plan coherence, spec alignment, invariant audit, optional independent audit lanes. | Project docs, exact invariant wording, repo-specific commands. |
| `.claude/skills/post-impl-audit.md` | `post-implementation-audit/SKILL.md` | candidate | Post-change verification: tests, syntax/import checks, stale-reference checks, independent audit lanes. | `uv` commands, module-specific checks, project-specific audit prompts. |
| `.claude/skills/design-invariants.md` | `engineering-invariants/SKILL.md` | candidate | Generic engineering invariants: no papering over, no weak tests, no hidden deferrals, no swallowed exceptions, no unjustified suppressions. | Manifest-as-oracle, repair boundary, game-data verification, frozen domain model list. |
| `.claude/skills/starsector-modding.md` subset | `starsector-modding/SKILL.md` | candidate | General Starsector 0.98a Java modding facts: sandbox, SettingsAPI file I/O, Janino limits, mission/plugin lifecycle, `org.json` checked exceptions. | Combat harness orchestration, instance manager details, cloud activation path, optimizer filtering rules. |

## Keep Local

| Workflow | Reason |
|---|---|
| `cloud-worker-ops` | Provider, account, AMI, Tailscale, Redis, and teardown SOP are operator-specific. A separate portable cloud-cost-safety skill may be extracted later. |
| `honest-evaluation` | Tied to this optimizer's campaign layout, scripts, closed opponent pool, and report schema. |
| Starsector optimizer invariants | Domain-specific contracts such as manifest-as-oracle, repair boundary, regime masking, and Java `EngineStats` ownership belong in this repo. |
| Combat harness orchestration | Depends on this mod's queue/result files, V2 placeholder-then-swap path, Robot coordinates, and local worker lifecycle. |

## Packaging Rule

A portable skill should be a directory with:

- `SKILL.md` for the concise core workflow.
- Optional `references/` for long details loaded only when needed.
- Optional `scripts/` only for deterministic repeated checks.

Do not promote a repo-local skill file verbatim. Extract the generic procedure,
then leave a thin local wrapper that names the repo's commands, paths,
invariants, and owning docs.

## Acceptance Checklist

A promotion is done only when:

- The target package has `SKILL.md` with generic trigger wording.
- Long examples or domain details are moved to `references/` and loaded only
  when needed.
- Repo paths, project commands, and project-specific invariants are removed
  from the portable package and retained in a local wrapper.
- The local wrapper names the portable skill it extends and lists only local
  overrides.
- The promoted skill avoids product-specific names except when documenting a
  literal path, command, compatibility note, or historical migration.
