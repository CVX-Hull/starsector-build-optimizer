---
plan_type: implementation
status: implemented
created: 2026-07-16
approved: 2026-07-16
implemented: 2026-07-16
owner: agent
related_docs:
  - docs/specs/22-cloud-deployment.md
  - docs/specs/30-honest-evaluator.md
  - src/starsector_optimizer/cloud_provider.py
  - src/starsector_optimizer/campaign.py
  - src/starsector_optimizer/honest_evaluator.py
  - src/starsector_optimizer/models.py
  - scripts/cloud/teardown.sh
  - scripts/cloud/final_audit.sh
implementation_commit: not_committed
post_impl_audit: passed
superseded_by: null
---

# Maintain-mode self-replenishing EC2 Fleet

## Goal

Give long-running cloud fleets **self-replenishment**: when AWS reclaims a spot
instance, the fleet relaunches a replacement instead of decaying monotonically.
Today every fleet is `Type="instant"` (one-shot, never replaced), so a 16 h
honest-eval run decayed 64 → 2 workers mid-run (2026-07-16 accounting oracle
pass). Introduce an **opt-in** `Type="maintain"` fleet, wired for the two
long-run paths (honest-eval + campaign studies), with the teardown, audit, and
drain changes that maintain-mode's persistent-fleet semantics require.

This is a **throughput/durability** fix, not a correctness fix: the Redis
janitor + `matchup_id` dedup already re-do a reclaimed worker's in-flight
matchup (spec 22 §Reliable-queue). Maintain-mode adds capacity *replacement* on
top of that correctness guarantee.

## Context and source docs

- Root cause: [cloud_provider.py:407](../../src/starsector_optimizer/cloud_provider.py) — `Type="instant"`.
- Dead config #1: `capacity_rebalancing` is parsed ([models.py:726](../../src/starsector_optimizer/models.py), [campaign.py:176](../../src/starsector_optimizer/campaign.py)) but **never wired** into `create_fleet` (`spot_options` carries only `AllocationStrategy`, [cloud_provider.py:388-390](../../src/starsector_optimizer/cloud_provider.py)). Spec 22:78 documents it neutrally ("EC2 Fleet CapacityRebalancing flag") **without noting it is inert** — misleading by omission, not a false "active" claim. This change wires it (maintain-only) and corrects the spec row.
- Dead config #2 (boy-scout, in scope): `CampaignConfig.fleet_provision_timeout_seconds` ([models.py:739](../../src/starsector_optimizer/models.py), parsed campaign.py) — "EC2 Fleet retry window before partial-fleet decision" — is **read nowhere in `src/`**. It is the natural knob for the maintain instance-poll window; this change wires it through `provision_fleet` rather than adding a parallel hardcoded constant.
- Two shipped mechanisms explicitly depend on the `Type="instant"` no-respawn invariant and are flagged "must be revisited if a maintain-type fleet is ever introduced":
  - honest-eval worker drain — spec 22 §"Worker drain (honest-eval)", lines 572-646. **The `WorkerDrainTicker` class is defined in [campaign.py:472-551](../../src/starsector_optimizer/campaign.py)** (spec 22:578 loosely says "in `honest_evaluator.main`"; that is only the construction site, `honest_evaluator.py:327` via `_make_worker_drain_thread`).
  - learned-batch scale-down-on-drain — spec 22 §9, lines 552-554. **Incompatible** with maintain (workers self-terminate → respawn loop) → learned-batch stays `instant`.
- Verified AWS API facts (2026-07-16, `aws ec2 ... help` + live `describe-fleets`):
  - `create_fleet(Type="maintain")` is **async**: response carries `FleetId`, `Instances` is empty/partial → must poll `describe_fleet_instances` toward target.
  - `SpotOptions.MaintenanceStrategies.CapacityRebalance.ReplacementStrategy ∈ {launch, launch-before-terminate}`.
  - `delete_fleets(FleetIds, TerminateInstances=True)` atomically deletes fleet + kills instances; `TerminateInstances=False` valid only for maintain/request.
  - `modify_fleet(FleetId, TargetCapacitySpecification={TotalTargetCapacity}, ExcessCapacityTerminationPolicy ∈ {termination, no-termination})`.
  - **A spot reclaim does NOT change a maintain fleet's `TargetCapacity`** — the fleet keeps the old target and relaunches to refill. (Load-bearing for the drain design — see step 8 / F1.)
  - `describe_fleets` supports **no `tag:` filter** (only `fleet-state`, `type`, `activity-status`, …) — but the response `Fleets[].Tags` carries the fleet's tags, so leaked-fleet discovery = list `fleet-state ∈ {submitted,active,modifying}` + **client-side** tag match. No on-disk FleetId persistence needed; teardown stays AWS-tag-derived.
- Scope ratified by user 2026-07-16: coverage = honest-eval + campaign studies; honest-eval drain = **rewire to fleet scale-in** (lower `TargetCapacity`, not terminate).

## Scope

1. **Config.** Add `CampaignConfig.fleet_type: str = "instant"` (defaulted field, placed in the defaulted block after `studies`; validated against a module-level `_ALLOWED_FLEET_TYPES = frozenset({"instant","maintain"})` matching the `_ALLOWED_PROVIDERS`/`_ALLOWED_SAMPLERS` convention at campaign.py:78-80). Wire the existing-but-dead `capacity_rebalancing` into maintain-mode provisioning (no-op under `instant`). Add both to the `load_campaign_config` pass-through opt tuple.
2. **Provider provisioning.** `provision_fleet` / `_create_fleet_in_region` gain `fleet_type`, `capacity_rebalance`, and `provision_timeout_seconds`. Maintain path captures `FleetId`, tags the fleet resource (`ResourceType:"fleet"`, carrying **both** `Project` and `Fleet` keys per the two-tag invariant), and polls `describe_fleet_instances` **toward the requested per-region target** (bounded by `provision_timeout_seconds`), returning the full discovered set so the `min_workers_to_start`/`total_matchup_slots` gate is sized correctly. `instant` path stays byte-for-byte identical (its tests must need zero edits).
3. **Provider fleet-lifecycle primitives.** New: `list_fleets_by_tag(project_tag, fleet_name=None, *, region)` (lists active states, client-side tag match; when `fleet_name` given, matches BOTH tags), `delete_fleets(fleet_ids, *, region, terminate_instances=True)`, `modify_fleet_target(fleet_id, target, *, region, excess_policy)`. Three new `@abc.abstractmethod`s + `HetznerProvider` `NotImplementedError` stubs + updates to the ABC-conformance test fakes (`test_cloud_provider.py:73,97`).
4. **Teardown inversion.** `terminate_fleet` (matches BOTH tags — passes `fleet_name`) + `terminate_all_tagged` (Project-only) delete matching fleets **first** (`delete_fleets(terminate_instances=True)`), then run the existing instance/LT/SG/volume passes as an idempotent backstop. Mirror in `scripts/cloud/teardown.sh` (fleet pass inserted before the current instances pass).
5. **Leaked-fleet audit.** `scripts/cloud/final_audit.sh` gains a fifth check (positioned before the per-region `continue`-on-describe-failure paths, wired into the existing `AUDIT_FAILED`→exit-2 vs `LEAKED`→exit-1 discipline, `NextToken`-paginated): any `submitted|active|modifying` fleet whose tags match the campaign → leak.
6. **Drain rewire (honest-eval, maintain only).** In `WorkerDrainTicker.tick` (campaign.py), when `fleet_type == "maintain"`, before terminating idle surplus in a region: resolve the region's FleetId (`list_fleets_by_tag`, cached), then `modify_fleet_target(fleet_id, new_target, excess_policy="no-termination")` where **`new_target = max(0, len(live_in_region) − k_region)` is derived from the observed live count the ticker already holds — NOT from the fleet's reported `TargetCapacity`** (which a reclaim leaves stale, causing respawn — F1). Then terminate the chosen idle ids. `instant` drain path unchanged. Requires threading `fleet_type`, `fleet_name`, and the provider fleet-ops into the ticker via its constructor + `_make_worker_drain_thread` (honest_evaluator.py:327).
7. **Opt-in wiring.** `cloud_runner.prepare_cloud_pool` passes `fleet_type=campaign.fleet_type`, `capacity_rebalance=campaign.capacity_rebalancing`, `provision_timeout_seconds=campaign.fleet_provision_timeout_seconds` into `provision_fleet`. Set `fleet_type: maintain` in `examples/accounting-hammerhead.yaml` + `examples/accounting-wolf.yaml`. No other production YAML is flipped in this plan.
8. **Spec 22 + spec 30 amendments** and doc grooming (enumerated in step 0).

## Out of scope

- **learned-batch (`phase7_learned_batch.py`) + `probe.py` stay `instant`.** learned-batch's self-terminate drain is fundamentally incompatible with respawn; probe is a boot-test. Their provisioning passes `fleet_type="instant"` (the default) unchanged.
- **`ReplacementStrategy="launch-before-terminate"` + `TerminationDelay`.** Use `"launch"` (simplest correct strategy for stateless workers). The proactive-rebalance variant is a later tuning knob.
- **Migrating teardown to a persisted FleetId manifest.** Not needed — `describe_fleets` + client-side tag match keeps teardown AWS-derived.
- **Campaign-wide (multi-study) shared fleet.** Per-study fleet ownership (spec 22 §"Fleet ownership") is unchanged; each study's regional fleets are independently maintained/torn down.
- **Refreshing aged-out workers under maintain (documented limitation, not solved).** A worker that self-exits at `max_lifetime_hours` only ends its *process*; the instance keeps running (idle), so the fleet does **not** see capacity drop and does **not** respawn it. Maintain replaces *reclaimed/interrupted* instances, not dead-process ones. For honest-eval this is masked (spec 30 raises `max_lifetime_hours` to cover the whole eval); for campaign studies it matches today's instant behavior (aged idle instance lingers until teardown). Named in the spec 22 amendment; process-liveness-driven replacement is a possible follow-up.

## Critical files

| File | Change |
|---|---|
| `src/starsector_optimizer/models.py` | `CampaignConfig.fleet_type` field (defaulted block) |
| `src/starsector_optimizer/campaign.py` | `_ALLOWED_FLEET_TYPES`; parse+validate `fleet_type`; pass-through opt tuple; **`WorkerDrainTicker` maintain branch + constructor params (fleet_type, fleet_name, provider fleet-ops)** |
| `src/starsector_optimizer/cloud_provider.py` | maintain provisioning (async + FleetId + fleet two-tag + CapacityRebalance + poll-to-target); `list_fleets_by_tag`/`delete_fleets`/`modify_fleet_target` (+ABC+Hetzner); teardown inversion (both `terminate_fleet` and `terminate_all_tagged`) |
| `src/starsector_optimizer/cloud_runner.py` | thread `fleet_type`/`capacity_rebalance`/`provision_timeout_seconds` into `provision_fleet` |
| `src/starsector_optimizer/honest_evaluator.py` | `_make_worker_drain_thread` (honest_evaluator.py:327) threads `fleet_type`/`fleet_name`/provider into the ticker |
| `scripts/cloud/teardown.sh` | fleet-delete pass first (JMESPath `--query`, not jq) |
| `scripts/cloud/final_audit.sh` | leaked-fleet check (AUDIT_FAILED discipline, paginated) |
| `examples/accounting-hammerhead.yaml`, `examples/accounting-wolf.yaml` | `fleet_type: maintain` |
| `docs/specs/22-cloud-deployment.md`, `docs/specs/30-honest-evaluator.md` | contract updates (step 0) |
| `tests/test_cloud_provider.py`, `tests/test_campaign.py`, `tests/test_honest_evaluator.py` | new + updated tests |

## Public concepts and canonical owners

- `fleet_type` — owner: `CampaignConfig` (models.py) + spec 22 §Config dataclasses.
- Maintain fleet lifecycle (create/delete/modify) — owner: `AWSProvider` + spec 22 §AWSProvider.
- Fleet-scale-in drain — owner: **`campaign.WorkerDrainTicker`** + spec 22 §Worker drain / spec 30.
- Leaked-fleet audit — owner: `scripts/cloud/final_audit.sh` + spec 22 §Teardown discipline / §Scripts.

## Step-by-step implementation sequence

**Step 0 — Spec first.** Amend, in this order:
- spec 22 §"Config dataclasses" — add `fleet_type` row; **correct** the `capacity_rebalancing` row (line 78) to state it is honored only under `fleet_type="maintain"`; note `fleet_provision_timeout_seconds` now governs the maintain instance-poll.
- spec 22 §"Two-tag scheme" (line 695) + §AWSProvider provisioning (line 717) — distinguish instant (no persistent fleet resource) from maintain (fleet resource tagged with both keys, discoverable).
- spec 22 §"CloudProvider ABC" — `provision_fleet` new params + the three new methods.
- spec 22 §AWSProvider `terminate_fleet`/`terminate_all_tagged` (lines 719, 721) — revise the documented order to **delete fleets first** (the current "terminate instances → LT → SG" order is respawn-unsafe under maintain).
- spec 22 §"Teardown discipline" (line 490) + §"Scripts" (lines 917-918) — add "fleets" to the audited/torn-down resource list.
- spec 22 §"Worker drain (honest-eval)" — the maintain target-lower-before-terminate branch + observed-live-count target rule; and the `max_lifetime_hours`→no-auto-refresh limitation note.
- spec 22 §9 (lines 552-554) — update the "must be revisited" note: maintain is opt-in; learned-batch stays instant because its self-terminate drain is respawn-incompatible.
- spec 30 — add `fleet_type` to the honest-eval inherited/pass-through fields list (the `load_campaign_config` + `dataclasses.replace` enumeration) and document the maintain drain.

1. **Config** (`models.py`, `campaign.py`): `fleet_type` defaulted field; `_ALLOWED_FLEET_TYPES` frozenset + `sorted(...)`-in-error validation in `load_campaign_config`; add `fleet_type` to the pass-through opt tuple. `capacity_rebalancing`/`fleet_provision_timeout_seconds` already parsed — no parse change, just downstream wiring.
2. **Provider constants**: module-level `_FLEET_INSTANCE_POLL_INTERVAL_SECONDS`, `_FLEET_ACTIVE_STATES = ("submitted","active","modifying")`, `_CAPACITY_REBALANCE_REPLACEMENT_STRATEGY = "launch"`, `_EXCESS_CAPACITY_NO_TERMINATION = "no-termination"`. The poll *timeout* comes from the threaded `provision_timeout_seconds` param, not a new constant.
3. **`_create_fleet_in_region`**: add `fleet_type`, `capacity_rebalance`, `provision_timeout_seconds`. Build `SpotOptions` (add `MaintenanceStrategies.CapacityRebalance={ReplacementStrategy:_CAPACITY_REBALANCE_REPLACEMENT_STRATEGY}` iff maintain ∧ capacity_rebalance). Add `ResourceType:"fleet"` TagSpec (both keys) iff maintain. Branch response: instant = unchanged (sync ids, zero→RuntimeError, transient-SG retry); maintain = capture `FleetId`, poll `describe_fleet_instances` every `_FLEET_INSTANCE_POLL_INTERVAL_SECONDS` until `len(ids) >= target` OR `provision_timeout_seconds` elapsed, return all discovered ids (partial fleet is then handled by the caller's existing `min_workers_to_start` gate, not by an early return).
4. **`provision_fleet`**: plumb the three new params through.
5. **`list_fleets_by_tag` / `delete_fleets` / `modify_fleet_target`** (+ABC abstract decls, +Hetzner stubs, +conformance-fake updates).
6. **Teardown inversion**: `terminate_fleet(fleet_name, project_tag)` → `list_fleets_by_tag(project_tag, fleet_name, region=…)` (both tags) → `delete_fleets(..., terminate_instances=True)`; `terminate_all_tagged(project_tag)` → `list_fleets_by_tag(project_tag, region=…)` (Project-only) → `delete_fleets(...)`. Both then run the existing `_terminate_by_tags`/LT/SG passes (idempotent backstop). Instant → empty fleet list → unchanged.
7. **`cloud_runner.prepare_cloud_pool`**: pass the three params from `campaign`.
8. **Drain rewire** (`campaign.WorkerDrainTicker`): constructor gains `fleet_type`, `fleet_name`, and access to the provider fleet-ops (already has the provider). In `tick`, maintain branch: group idle surplus by region; per region resolve+cache FleetId via `list_fleets_by_tag(project_tag, fleet_name, region)`; `modify_fleet_target(fleet_id, max(0, len(live_in_region) − k_region), excess_policy=_EXCESS_CAPACITY_NO_TERMINATION)`; then terminate the k_region idle ids. `instant` → existing terminate-only path. Thread the new params through `_make_worker_drain_thread` (honest_evaluator.py:327).
9. **Shell**: `teardown.sh` fleet pass (`describe-fleets --filters Name=fleet-state,Values=submitted,active,modifying --query "Fleets[?Tags[?Key=='Project'&&Value=='$TAG']].FleetId"` → `delete-fleets --terminate-instances`) **before** the instances pass; note `delete-fleets` is async so the later instance pass stays an idempotent straggler-catcher. `final_audit.sh` fifth check (same discovery, paginated, wired to `AUDIT_FAILED`/`LEAKED`). Shellcheck-clean.
10. **YAML opt-in**: `fleet_type: maintain` in the two accounting YAMLs.

## Tests and mechanical gates

New/updated tests (write failing first):
- `test_cloud_provider.py`:
  - maintain `create_fleet` kwargs: `Type="maintain"`; `MaintenanceStrategies.CapacityRebalance` present iff `capacity_rebalance`; `ResourceType:"fleet"` TagSpec present with **both** keys.
  - maintain async poll: mock `describe_fleet_instances` returning instances incrementally → returns the full set once `>= target`; returns the partial set at timeout (does NOT early-return at ≥1).
  - instant path: no `MaintenanceStrategies`, no fleet TagSpec, sync ids — **assert the existing instant tests are unchanged** (byte-for-byte scope claim).
  - `list_fleets_by_tag`: client-side tag match incl. fleets with no `Tags` / non-matching Project / matching Project but wrong Fleet (both-tag mode must exclude it).
  - `delete_fleets` / `modify_fleet_target` param shapes.
  - teardown ordering: under maintain, `delete_fleets` called **before** `terminate_instances`; `terminate_fleet` passes `fleet_name` so a sibling-fleet resource is **not** deleted; instant → no fleet calls.
- `test_campaign.py`: `fleet_type` default `"instant"`, parse `maintain`, reject invalid (`ValueError`, sorted-set message); `capacity_rebalancing` fixtures updated to assert maintain-only wiring; `WorkerDrainTicker` maintain branch lowers per-region target (derived from observed live count) before terminate, clamps to `max(0,…)`; instant branch unchanged; keep-floor preserved.
- `test_honest_evaluator.py`: `_make_worker_drain_thread` threads `fleet_type`/`fleet_name`; `--no-drain` unchanged.

Mechanical gates: `uv run pytest tests/ -v`; `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run deptry .`; `shellcheck scripts/cloud/teardown.sh scripts/cloud/final_audit.sh`; design-invariants grep (no magic numbers — poll interval/states/replacement-strategy/excess-policy are module constants; poll timeout is the threaded config field).

**Before first maintain launch (operational gate, not code):** src/ changes flip `WorkerSourceSha` → **AMI re-bake required** (`scripts/cloud/bake_image.sh`) before any `fleet_type: maintain` launch/resume, then update `ami_ids_by_region`. Pre-launch: post-impl audit + stale-AWS-resource sweep (standing gates).

## Review findings and dispositions

Sources: self-review (Phases 1-4) + 3 fresh-eye sub-agents (pattern-consistency, spec-alignment, engineering/design-invariants), 2026-07-16.

**Blocking — fixed in plan:**
- B1 (respawn defeats drain): drain now derives new per-region target from **observed live count**, not stale `describe_fleets` `TargetCapacity` (scope 6, step 8). Clamped `max(0,…)`.
- B2 (async under-sizing): maintain poll now targets requested capacity bounded by `provision_timeout_seconds`, returns the full set; partial handled by the existing `min_workers_to_start` gate (scope 2, step 3).

**Should-fix — fixed in plan:**
- `WorkerDrainTicker` relocated to its true home `campaign.py` (owner, critical-files, step 8, tests).
- `terminate_fleet` passes `fleet_name` (both-tag match) so sibling studies' fleets survive; test added.
- Second dead field `fleet_provision_timeout_seconds` wired (threaded param) instead of a parallel constant.
- Shell uses JMESPath `--query`, not `jq` (no new dep).
- `final_audit.sh` fifth check wired to `AUDIT_FAILED`/exit-2 + pagination.
- `_ALLOWED_FLEET_TYPES` frozenset convention; `fleet_type` placed in the defaulted field block.
- Fleet TagSpec carries both `Project` and `Fleet`; conformance-test fakes updated for the new abstractmethods.
- Corrected the spec-22:78 rationale (misleading-by-omission, not a false "active" claim).
- Step 0 now enumerates every touched spec section (§Two-tag scheme, §Teardown discipline, §Scripts, spec 30 inherited-fields).
- `max_lifetime_hours`↔maintain interaction documented as a named limitation (out-of-scope).

**Nits — addressed:** region keyword-only call fixed; `ExcessCapacityTerminationPolicy` values hoisted to constants; `capacity_rebalance`(param) vs `capacity_rebalancing`(field) spelling noted at the pass-through; delete-fleets async/idempotent-backstop note added.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-16
- Findings: Phases 1-4 self-review clean after revision; all sub-agent findings triaged above.
- Dispositions: 2 blocking + 11 should-fix + nits all resolved in the plan; no findings rejected.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-16
- Agents:
  - Pattern Consistency: findings (11) — resolved
  - Spec Alignment: findings (9) — resolved
  - Engineering & Design Invariants: findings (F1-F7 + positives) — resolved
- Findings: consolidated in "Review findings and dispositions".
- Dispositions: all valid findings folded into scope/steps/tests; B1 and B2 (blocking) fixed; no test-weakening introduced.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

- 3 independent audit sub-agents (post-impl-audit skill): (a) provisioning async/FleetId correctness + instant-path invariance (diff instant tests = zero changes); (b) teardown/audit fleet-leak coverage across all 4 regions; (c) drain scale-in correctness (target from live count, no respawn, no sibling-fleet deletion, keep-floor preserved).
- design-invariants mechanical checks.
- Confirm instant callers (learned-batch, probe) unchanged by diffing their `provision_fleet` call sites.

### Post-impl audit result (2026-07-16) — PASSED

- **Auditor A (provisioning):** clean — async poll-to-target, conditional CapacityRebalance/fleet-TagSpec gating, byte-for-byte instant invariance (zero test deletions), no-FleetId RuntimeError all verified. 2 nits.
- **Auditor B (teardown/audit):** clean — fleet-delete-before-backstop ordering, both-tag `terminate_fleet` (sibling fleets survive), `list_fleets_by_tag` client-side match + pagination, `teardown.sh` Pass-0 + `final_audit.sh` fifth check with AUDIT_FAILED/LEAKED discipline, shellcheck-clean. 2 non-actionable nits.
- **Auditor C (drain):** clean — target from observed live count (not stale `TargetCapacity`), modify-before-terminate ordering, instant branch unchanged, keep-floor preserved, fleet-unresolvable + clamp-at-zero edges, threading correct. 2 nits.
- **Zero blocking, zero should-fix.** Actionable nits applied: maintain partial-error WARN (observability parity with instant); per-region sequential-timeout note (code comment + spec); `_lower_fleet_target` single-fleet-per-project-tag assumption comment (forward-looking guard for a future campaign-drain extension).
- **Mechanical checks:** no new TODO/FIXME/skip/type-ignore; no bare literals in new bodies (module constants + threaded config); `CampaignConfig` still frozen; `fleet_type` defaulted.
- **Gates after nit fixes:** `pytest` (255 passed in the 3 touched test files; full suite 1246 passed / 1 pre-existing skip earlier), `ruff check`/`ruff format --check`/`mypy`/`deptry` clean, `shellcheck` clean.

**Not committed** (per "commit only when the user asks"). **Operational gate before any `fleet_type: maintain` launch:** src/ changes flip `WorkerSourceSha` → AMI re-bake + `ami_ids_by_region` update + standing pre-launch audit/sweep.

## Retirement checklist

- [ ] `status: implemented`, `implemented:` date, `implementation_commit`, `post_impl_audit`.
- [ ] Move to `.claude/plans/archive/2026/`.
- [ ] Groom `docs/roadmap.md`; close the fleet-durability portion of `task_f6873a0c`.
- [ ] Update memory (fleet durability + the untagged-worker note now resolved).
