---
type: always-loaded
status: shipped
last-validated: 2026-05-10
---

# Starsector Ship Build Optimizer

Automated ship build discovery for Starsector using Bayesian optimization and combat simulation.

> **Empirical-claims status (2026-05-10):** All Phase 5A–5F empirical magnitudes are pending re-validation under the V2 combat-harness loadout fix (commit `8a5b968`). Design rationale, phase-completion status, and architectural decisions are unchanged. See [docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](docs/reports/2026-05-10-v1-loadout-bug-invalidation.md). Documentation conventions: [docs/CONVENTIONS.md](docs/CONVENTIONS.md).

## Phase status

### Phase 1 — Data layer (complete)
Game data parsing, search space, constraint repair, heuristic scoring, variant generation.

### Phase 2 — Combat harness mod (complete)
Java AI-vs-AI combat simulation with JSON result export.

### Phase 3 — Instance manager (complete)
N parallel Starsector instances via Xvfb, batch evaluation, health monitoring.

### Phase 4 — Optimizer integration (complete)
- Optuna TPE, opponent pool, heuristic warm-start (default-disabled post-Phase-7-prep), parameter importance.
- CatCMAwM was evaluated and removed 2026-04-19: the library requires ≥1 continuous variable (`x_space must be shape (n, 2), got (0,)`), and this codebase's search space is fully categorical + integer (`weapon_<slot_id>`, `hullmod_<mod_id>`, `flux_vents`, `flux_capacitors`). See [`optimizer.py`](src/starsector_optimizer/optimizer.py) docstring.
- A TPE-vs-CatCMAwM-vs-random sampler benchmark was scoped at ~$2 then skipped — without CatCMAwM there's no meaningful Bayesian alternative, and TPE-vs-random has a foregone conclusion.

### Phase 5 — Signal quality (5A–5F complete; 5G deferred)

- **5A** TWFE deconfounding + control variate + rank shape. Design: [`docs/reference/phase5-signal-quality.md`](docs/reference/phase5-signal-quality.md), [`phase5a-deconfounding-theory.md`](docs/reference/phase5a-deconfounding-theory.md).
- **5B** WilcoxonPruner + ASHA. Design: [`phase5-signal-quality.md`](docs/reference/phase5-signal-quality.md).
- **5C** Anchor-first + incumbent-overlap opponent selection. Design: [`phase5c-opponent-curriculum.md`](docs/reference/phase5c-opponent-curriculum.md).
- **5D** EB shrinkage of α̂. Replaces the scalar control variate with empirical-Bayes shrinkage of `α̂_TWFE` toward a covariate regression prior. **Fusion paradigm**: `α̂_TWFE` and the prior features are noisy measurements of the same latent α, combined by Bayes rule (HN + triple-goal rank correction; closed-form two-level Gaussian model). Per-trial posterior uncertainty logged in JSONL `eb_diagnostics` (σ²_TWFE, σ²_EB, τ̂², γ̂, kept_cov_columns) for downstream CI reconstruction. The original conditioning-paradigm v1 (CUPED / FWL / PDS lasso / ICP) was refuted by Cinelli-Forney-Pearl 2022 "Case 8" bad-control analysis: the scorer components are noisy proxies of the estimand α, not orthogonal covariates of Y. The fusion paradigm replacement is documented in [`phase5d-covariate-adjustment.md`](docs/reference/phase5d-covariate-adjustment.md) §4.5. Post-Phase-7-prep covariate refactor: 7→10 dims (drops `composite_score`, adds 3 engine-truth SETUP reads + 1 Python-raw structural). Post-ship TTK-signal investigation deferred to Phase 5F as opt-in `eb_extra_covariates`.
- **5E** Box-Cox output warping. Replaces quantile-rank A3. `scipy.stats.boxcox` fits λ on the post-5D EB posterior-mean population every `_finalize_build` call (refit cadence chosen over batched: cache-coherence simpler, ~1ms cost negligible). Min-max rescaled to `[0, 1]` for JSONL schema stability. Below `ShapeConfig.min_samples=8` falls through to min-max scaling; non-finite input raises `ValueError` (fail-fast on upstream NaN). JSONL fields `shape_lambda` + `shape_passthrough_reason` + per-trial λ logger + end-of-run summary log. Effect on ρ_truth is near-zero by design (both transforms monotone) — the mechanical win is ceiling-saturation reduction and top-k overlap recovery. CAT Fisher-info opponent selection (J strategy) deferred — observation-side change, revisit post-5F. Design: [`phase5e-shape-revision.md`](docs/reference/phase5e-shape-revision.md).
- **5F** Regime-segmented optimization. User-selectable loadout regime hard-masks the hullmod and weapon catalogues at `search_space.py` construction time. `RegimeConfig` with four presets (`early` (default) / `mid` / `late` / `endgame`); one Optuna study per `(hull, regime)` named `f"{hull_id}__{regime.name}"`; cross-regime warm-start via `--warm-start-from-regime` (enqueues feasibility-checked prior-regime incumbents through `repair_build` + `is_feasible`). **Open-world framing**: regime filters OUR loadout only; opponents drawn from the full hull-size-matched pool; hull choice user-controlled. Default `early` is the conservative component-availability baseline, not a difficulty tier. Framed as **CMDP feasibility alignment** (Altman 1999, Huang & Ontañón 2020), not reward-shaping. Grounded in Jaffe 2012 restricted-play, Csikszentmihalyi flow / Ryan-Rigby PENS / Koster mastery-decay, Suits' lusory attitude / Caillois' agon, and Alex Mosolov's stated design intent. Rejected alternatives: scalar penalty (bad-control contamination like 5D v1), archive-over-single-run (insufficient budget), curriculum across regimes (Bengio 2009 doesn't apply), multi-fidelity BOCA (requires same-x), Pareto / NSGA-II (single in-regime recommendation wanted), hull-filter presets (open-world), Weitzman reservation-value / PBGI (deferred — needs per-component posteriors). Full chain: [`phase5f-regime-segmented-optimization.md`](docs/reference/phase5f-regime-segmented-optimization.md).
- **5G** Adversarial opponent curriculum (PSRO-style pool growth) — **deferred**. Renumbered from 5F. Research complete; revisit post-5E/5F if exploit convergence persists in unrestricted `endgame` regime.
- **Empirical status**: All Phase 5A–5F effect magnitudes pending re-validation. See [docs/reports/INDEX.md](docs/reports/INDEX.md) for re-validation reports as they land.

### Phase 6 — Cloud Worker Federation (infrastructure shipped; Tier-1 + Tier-2 live-validated)

Phase 6 ships AWS spot-fleet evaluation while the workstation keeps every Optuna Study local. Design: [`docs/reference/phase6-cloud-worker-federation.md`](docs/reference/phase6-cloud-worker-federation.md). Spec: [`docs/specs/22-cloud-deployment.md`](docs/specs/22-cloud-deployment.md). SOP: [`.claude/skills/cloud-worker-ops.md`](.claude/skills/cloud-worker-ops.md).

**Topology**: workstation = sole orchestrator (every Optuna Study, workstation-local Redis on tailnet interface, per-study Flask `POST /result` listener with bearer auth + `matchup_id` dedup); workers = pure `MatchupConfig → CombatResult` evaluators on AWS c7a.2xlarge spot via `worker_agent.py`. The worker module is forbidden from importing `repair` (AST-enforced test). `EvaluatorPool` ABC is the cross-backend contract (`LocalInstancePool` / `CloudWorkerPool`). `CloudProvider` ABC ships with `AWSProvider` and a `HetznerProvider` `NotImplementedError` stub.

**Provider + fleet**:
- `AWSProvider` per-fleet API: `provision_fleet` / `terminate_fleet` (targeted teardown) / `terminate_all_tagged` (campaign-wide sweep backstop).
- Two-tag scheme: every resource carries `Project=starsector-<campaign>` AND `Fleet=<fleet_name>`; LT/SG names are `f"{project_tag}__{fleet_name}"` so multiple studies in one region never collide.
- `cloud_userdata.render_user_data` writes user-data with umask 0077, Tailscale authkey via stdin, IMDSv2 WORKER_ID override inserted between `chown` and `systemctl start` so `set -euo pipefail` halts boot if IMDS is unreachable; `sed -i` + append guarantees one canonical env line.

**Fleet ownership lives in the study subprocess** (`scripts/run_optimizer.py --worker-pool cloud` → `cloud_runner.run_cloud_study`):
- Reads env-plumbed `STARSECTOR_WORKSTATION_TAILNET_IP` / `STARSECTOR_BEARER_TOKEN` / `STARSECTOR_TAILSCALE_AUTHKEY` / `STARSECTOR_PROJECT_TAG` via `_require_env` (ValueError with remediation pointer, not KeyError).
- Renders UserData → `provider.provision_fleet(fleet_name=study_id, project_tag=project_tag, ...)` → enters `with CloudWorkerPool` → runs Optuna study → `finally: provider.terminate_fleet(...)`.
- Per-study ownership: a study crash reaps only that study's fleet. Per-study bearer tokens (`secrets.token_urlsafe(32)`) isolate compromise to a single study.

**Pool concurrency abstraction**:
- `CloudWorkerPool` takes a single `total_matchup_slots` parameter (= `workers_per_study × matchup_slots_per_worker`); semaphore matches worker-side capacity exactly.
- The worker agent spawns `matchup_slots_per_worker` Redis consumer threads sharing one `LocalInstancePool(num_instances=matchup_slots_per_worker)` so every JVM stays busy.
- A dedicated heartbeat thread writes `os.getloadavg()` + `cpu_count` to `worker:<project_tag>:<worker_id>:heartbeat` so the orchestrator can verify `matchup_slots_per_worker` fits the box.
- Healthy `load_avg_1min` band on c7a.2xlarge: ∈ [3, 8]. Sustained `load_avg_1min > cpu_count` indicates over-subscription; sustained `< 3` indicates under-utilization. The Tier-2.5 multi-worker smoke gate uses this as a health invariant.

**Redis key namespacing**: every queue + heartbeat key is prefixed with `project_tag` (`queue:<project_tag>:<study_id>:source`, etc.); `_preflight` SCAN+DELs the `project_tag` prefix at startup so re-running a campaign with the same name never inherits stale processing-list items.

**Workstation dev env is rootless-capable**:
- `scripts/cloud/devenv-up.sh` / `devenv-down.sh` bring up userspace-mode tailscaled + redis-server as the current user (no sudo, no kernel TUN); Redis + Flask port range exposed via `tailscale serve` TCP proxies.
- `_preflight` auto-detects the userspace socket at `~/.local/state/starsector-cloud/tailscale/tailscaled.sock` (or `$STARSECTOR_TAILSCALE_SOCKET`) and accepts kernel-mode (tailnet-IP bound to a local interface) or userspace-mode (127.0.0.1 + `tailscale serve` proxy verified via `tailscale serve status`) as a valid Redis tailnet-exposure path.

**Four-layer teardown** (defence in depth):
1. study subprocess `finally: terminate_fleet`,
2. `CampaignManager` `finally: terminate_all_tagged` (sweep backstop),
3. `atexit.register`,
4. `launch_campaign.sh` `trap EXIT` runs `teardown.sh` + `final_audit.sh`.

**Operations**:
- `CostLedger`: append-only JSONL with `fsync` per row, `BudgetExceeded` at hard cap, warn thresholds `(0.5, 0.8, 0.95)`.
- `CampaignManager._tick_ledger` SCANs `worker:<project_tag>:*:heartbeat`, attributes spot-price cost per `(region, instance_type)` via in-process cache (`spot_price_cache_ttl_seconds=300`), capped at `heartbeat_stale_multiplier=3` for staleness.
- Janitor resets `enqueued_at` on re-queue and tracks `requeue_count` with `max_requeues=5` drop + ERROR log.
- Worker heartbeat carries region + instance_type from IMDSv2 (fallback `"unknown"` on failure → self-identifying zero-rate ledger row).
- Preflight asserts AMI `GameVersion` AND `ModCommitSha` tags match `manifest.constants.{game_version, mod_commit_sha}` — dual check catches stale-mod AMIs even when the engine version is unchanged.

**Secrets hygiene**: `CampaignConfig.__repr__` redacts `tailscale_authkey_secret`, `WorkerConfig.__repr__` redacts `bearer_token`; subprocess env dicts never logged (grep invariant). Campaign YAML supports field-scoped `${VAR}` env-substitution for `tailscale_authkey_secret` only; campaign names are regex-validated against `^[a-zA-Z0-9._-]{1,64}$` for AWS LT naming compatibility.

**Packer AMI bakes** (`scripts/cloud/packer/aws.pkr.hcl`): game files (`game/starsector/`), the combat-harness mod JAR, the uv venv, the Tailscale client, and `x11-xserver-utils` (for the LWJGL XRandR warmup that the launcher pre-fights). AMI tags `GameVersion` + `ModCommitSha` are checked at preflight against `manifest.constants` to block stale-mod AMIs even when the engine version is unchanged.

**Tier-2 live-run gate** (4 hard checks; each must hold): `launch_campaign.sh examples/smoke-campaign.yaml` exits 0; `final_audit.sh smoke` exits 0; `data/campaigns/smoke/ledger.jsonl` has ≥ 1 `worker_heartbeat` row; the Optuna study DB has ≥ 1 `TrialState.COMPLETE`. Smoke is the gate that confirms a fresh AMI bake, a fresh tailnet, and a fresh code path all line up end-to-end.

**Operator SSH on workers**: optional `STARSECTOR_DEBUG_SSH_PUBKEY` env var causes `cloud_userdata.render_user_data` to append the operator's public key to `/home/ubuntu/.ssh/authorized_keys` at boot. **Do not use `tailscale up --ssh`** — that hijacks port 22 for tailscaled's identity-based SSH server, which then silent-denies under a default-permissive personal tailnet ACL. Phase 7.5 (R2) plans Tailscale ACL-as-code via the Terraform provider so `tailscale ssh` works on first smoke without manual admin-panel ACL editing; until then, use the SSH-pubkey injection.

**Concurrent-dispatch correctness fixes** (regression-tested):
- `AWSProvider._ensure_security_group` blocks on `security_group_exists` boto3 waiter; `_create_fleet_in_region` retries `create_fleet` on `InvalidGroup.NotFound` with `any(transient)` predicate (Fleet-service replication lag under N≥6 concurrent studies).
- `_apply_eb_shrinkage` guard uses `len(_completed_records)` (fully-finalized trials) instead of `score_matrix.n_builds` (which counts trials with ≥1 matchup result and would feed an under-sized OLS).
- `cloud_runner.py` study_id includes sampler (`f"{hull}__{regime}__{sampler}__seed{seed}"`); `scripts/run_optimizer.py` eval_log_path is per-study (`data/logs/<study_id>/evaluation_log.jsonl`) so concurrent subprocesses don't destroy attribution.
- Tests: [`tests/test_cloud_provider.py::TestFleetProvisionSGPropagation`](tests/test_cloud_provider.py) + `tests/test_optimizer.py` + `tests/test_run_optimizer_cloud.py`.

**Tier-2 launcher debug (2026-05-09)** rooted in three Xvfb / Java-AWT quirks:
- `xdotool windowactivate` requires the EWMH `_NET_ACTIVE_WINDOW` atom (only set by a window manager) — fails under bare Xvfb. Use `windowfocus` (XSetInputFocus, WM-free).
- `xdotool key --window <wid>` dispatches via XSendEvent which Java AWT silently filters as a synthetic-event-injection defense — XTest (no `--window` flag) produces real-looking keystrokes.
- The Play JButton is not AWT default-focused — even XTest Return alone hits the JFrame and goes nowhere. A coordinate-based mouse click computed from `xdotool getwindowgeometry` sidesteps the focus chain.
- Final dispatch: `windowmap → windowfocus → mousemove(X+W·0.5, Y+H·0.7) → click 1 → key Return` (belt-and-suspenders); fractions live in `InstanceConfig.launcher_play_button_{x,y}_fraction` so future game-version layout shifts can be retuned from `<work_dir>/launcher_dispatch.log` evidence.

**Java-only fast-iteration path** (added 2026-05-10): optional `STARSECTOR_MOD_JAR_OVERRIDE_URL` + `STARSECTOR_MOD_JAR_OVERRIDE_SHA256` env vars cause `cloud_runner.py` → `cloud_userdata.render_user_data` to emit a tailnet-curl + sha256-verify + `install` block that overlays the AMI-baked combat-harness JAR before `systemctl start`. `scripts/cloud/serve_mod_jar.sh` builds the JAR, exposes it on a `tailscale serve` TCP proxy, and prints both env vars (also `--env` mode for `eval`-friendly export). Loop becomes `./gradlew jar` → `eval "$(serve_mod_jar.sh --env)"` → relaunch. Fail-closed: any download error, sha mismatch, or chown failure halts boot via `set -euo pipefail`; `_validate_jar_override` raises `ValueError` if URL or SHA256 set without the other (no silent verification skip). AMI rebakes still required for game files, Python (`uv.lock`), or systemd-unit changes.

**Combat-harness V2 loadout fix (2026-05-10)** — final design:
- Deploy a stock placeholder via `addToFleet(side, anyStockVariantForHull, FleetMemberType.SHIP, fleetMemberId, false)`.
- Swap to the optimizer-generated variant via `member.setVariant(VariantBuilder.createVariant(spec), false, true)` BEFORE the deployment screen processes the fleet — pre-deployment swap propagates to the physical ship.
- `CombatHarnessPlugin.doSetup` sets CR live on each deployed `ShipAPI` (`setCurrentCR` + `setCRAtDeployment` + `setRetreating(false, false)`) — `getCurrentCR()` does NOT inherit from the FleetMember's repair tracker and CR=0 triggers auto-retreat.
- The `LoadoutDiagnostic` block in `doSetup` is retained as a permanent canary; the orchestrator emits one `LOADOUT_OK` INFO per matchup and a structured `LOADOUT_MISMATCH` WARN on any field divergence.
- Validated via [`scripts/cloud/loadout_ab_test.py`](scripts/cloud/loadout_ab_test.py): ARMED Hammerhead × 3 dealt damage and won; NAKED Hammerhead × 3 dealt EXACTLY 0.0 damage and lost.
- Master invalidation report: [docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](docs/reports/2026-05-10-v1-loadout-bug-invalidation.md).

**Logging conventions** (post-impl audit, 2026-05-10):
- High-volume Java `[FIGHT_TICK]` per-second state dump is gated behind `MatchupConfig.debug_dumps_enabled` (default false; smoke YAMLs and the AB test flip on).
- One-shot SETUP `[SHIP_DUMP]` and end-of-match `[WIN_DUMP]` lines stay always-on (load-bearing for any future loadout regression).
- `cloud_worker_pool.py` janitor + Flask `/result` handlers use `logger.exception` so caught exceptions surface their stack trace.

**Deferred follow-ups** (operational backstops, orthogonal to phase progression): plateau detector, tag-based sweeper cron, CloudWatch billing alarm. From the 2026-04-19 audit (`H` = high-severity, `M` = medium): **H2** POST-before-register race behind the unreachable retry path, **M1** janitor `enqueued_at` ping-pong under steady-state slow matchups, and the concurrency-shakedown gap between Tier-2.5 smoke and Phase 7 prep — each captured with reproduction + proposed fix in [docs/reports/2026-04-19-phase6-deferred-audit.md](docs/reports/2026-04-19-phase6-deferred-audit.md). H1 (`TimeoutTuner` dormancy) is moot — the module was deleted in the Phase-7-prep refactor. Two follow-ups routed to Phase 7.5: (R1) launcher OCR fallback for drift-proofing across Starsector minor-version updates; (R2) Tailscale ACL-as-code via the Terraform provider so operator `tailscale ssh` works from a userspace-mode workstation.

**Backwards-compat policy**: no compat layer — pre-Phase-6 `scripts/cloud/deploy.sh` etc. are deleted; `create_fleet` / `provision_fleet(config, ...)` removed; `partial_fleet_decide` / `PartialFleetAbort` removed.

**Empirical status**: throughput rates (matchups/hr/VM, trials/hr, cloud-vs-local speedup) and all derived $ figures pending re-validation under V2 setup-time overhead. See [docs/reports/INDEX.md](docs/reports/INDEX.md).

### Phase-7-prep refactor — manifest-as-oracle (complete, 2026-04-19)

- Eliminates hand-coded `hullmod_effects.py` (14 audit-discovered game-rule drift bugs) and `timeout_tuner.py` (Phase 3.5, dormant after WilcoxonPruner shipped at Phase 5B). Both modules deleted.
- All game-rule data flows from `game/starsector/manifest.json` written by Java [`ManifestDumper`](combat-harness/src/main/java/starsector/combatharness/ManifestDumper.java) and read by Python [`GameManifest.load()`](src/starsector_optimizer/game_manifest.py) — single source of truth, see [`docs/specs/29-game-manifest.md`](docs/specs/29-game-manifest.md).
- The Java mod's `optimizer_arena` mission branches into a probe-mode path on detecting `combat_harness_manifest_request`. Spawns a minimal stub (wolf + lasher) to pass the deployment screen, then `CombatHarnessPlugin` runs a **two-phase PROBE_ITERATE** (schema v2):
  - **BASE phase** iterates non-skipped hulls (skip = `HullSize.FIGHTER/DEFAULT` + `ShipTypeHints.{STATION, MODULE, SHIP_WITH_MODULES, HIDE_IN_CODEX}`) in batches of 10/frame via `createEmptyVariant` → `createFleetMember` → `spawnFleetMember` off-map at `x=-50000`. Calls `HullModEffect.isApplicableToShip(ship)` twice per ship (determinism canary → `constants.stateful_hullmods`; empty in vanilla). Records `hulls[H].applicable_hullmods`. Despawns via `engine.removeEntity` + `CombatFleetManagerAPI.removeDeployed`.
  - **CONDITIONAL phase** iterates each hull's applicable set pair-wise (install A, re-probe B) to build `hulls[H].conditional_exclusions[A] ⊇ {B that drop}`.
- Replaces the v1 hullmod-level `applicable_hull_sizes` + `incompatible_with` fields (both deleted; hull-size / shield-type / carrier / phase / civilian / built-in filters all subsumed by per-hull engine probe). Damage multipliers (shield/armor/hull) sourced from `DamageType.getShieldMult/getArmorMult/getHullMult` API — not hardcoded. Regenerated via `uv run python scripts/update_manifest.py --timeout 600`.
- Python: `_build_covariate_vector` goes 7→10 dims (drops `composite_score` due to drift-prone registry contributing 11–22% of |γ̂|; adds 3 engine-truth SETUP reads `eff_hull_hp_pct` / `ballistic_range_bonus` / `shield_damage_taken_mult` + 1 Python-raw structural `op_used_fraction` from manifest-authoritative OP costs). `engine_stats=None` is now a hard `AssertionError` (no Python fallback — mixing sources biased γ̂). `warm_start_n` defaults to 0 (heuristic prior dropped). `EngineStats` grown 3→6 fields.
- Operational: `MountType.HIDDEN` renamed to `OTHER` to match manifest vocabulary. Pre-commit hook extended to gate Python-only `game_manifest.py` edits (schema v1-vs-v2 desync prevention).

### Phase 7 — Structured search-space representation (planned)

Renumbered from 6. Replaces the Phase 4 Optuna TPE surrogate with a custom BoTorch Gaussian Process whose kernel composes subspace-specific priors. Full grounding + rejected alternatives: [`docs/reference/phase7-search-space-compression.md`](docs/reference/phase7-search-space-compression.md).

**Kernel composition**:
- SAAS sparsity (Eriksson-Jankowiak 2021 [arXiv:2103.00349](https://arxiv.org/abs/2103.00349)) on the hullmod-boolean subspace.
- Transformed-overlap kernel (Garrido-Merchán & Hernández-Lobato 2020) + 7-attribute Matérn on weapon slots.
- 5-dim slot-feature Matérn (`forward_projection`, `arc_width`, `is_turret`, `lateral_offset`, `longitudinal_offset`) for cross-slot kernel similarity.
- Opponent-summary features (has-missiles-frac, has-fighters-frac, mean-armor-rating) injected into *small-slot* posteriors only — preserves opponent-conditional small-slot addressability (load-bearing empirical constraint from community meta).
- BaCO-style gated-sentinel for conditional slots (Hellsten 2024 [arXiv:2212.11142](https://arxiv.org/abs/2212.11142), §4.3).
- ICM per-item and per-slot residuals (Bonilla 2007, Álvarez 2011) that shrink to zero unless data forces a quirk — same fusion paradigm as 5D.

**Warmup**: BOCA-style 30-trial RF-importance pilot (Chen 2021) empirical-Bayes-initializes SAAS lengthscales.

**πBO priors** (Hvarfner 2022 [arXiv:2204.11051](https://arxiv.org/abs/2204.11051)) over nine community-stable role modes (SO brawler, long-range sniper, kinetic-HE brawler, broadside, turret-flex, burst-missile, PD-carrier, flanker, phase striker — stable across Starsector 0.95→0.98).

**Hull-conditional activation**: per-hull feasibility mask (computed at hull-load from `.ship` JSON + ship-system registry) drops infeasible modes to zero weight (Wolf can't realize broadside or turret-flex). Initial weights uniform over feasible modes (no meta-hull coverage bias). Self-correcting mixture weights (Hvarfner 2023 [arXiv:2304.00397](https://arxiv.org/abs/2304.00397)) online-downweight modes that disagree with per-hull data. Community meta supplies the *vocabulary of modes*, not the *per-hull weights*.

`range` and `op_cost` are hull-size-normalized so mode definitions transfer across FRIGATE→CAPITAL without rescaling; `hull_size` enters the kernel as an ordinal context feature.

**AI pilotability absorbed by the same mechanism**: combat sim is AI-vs-AI by construction and the AI mispilots several community-top archetypes (SO brawler, phase striker, burst-missile) designed for player piloting. Rather than hardcode AI-compatibility flags (rejected §4.16: AI behavior changes across patches and pilotability interacts with hull), the self-correcting mixture lets AI-hostile modes empirically collapse their weight under simulation evidence. Player-piloted flagship optimization is **out of Phase 7 scope** (§4.17: would require engine-level input injection) and deferred indefinitely.

**Simulation fidelity floors** (documented in §2.10 of Phase 7 doc, spec 13, spec 04, spec 29): the optimizer does not populate fighter wings (carrier bays deploy empty; LAUNCH_BAY branch of PD-carrier archetype currently infeasible — only destroyer-escort-smalls realizes), does not configure weapon groups (`VariantBuilder` uses `autoGenerateWeaponGroups()`), and does not apply officer skills (default-personality, un-officered). All three are absorbed by the self-correcting mixture.

**Integration**: BoTorch-as-Optuna-sampler. Game-update invariant by construction — new weapons inherit the attribute-kernel prior zero-shot via the 7-attribute vector.

**Rejected alternatives**: NAS weight-sharing (no trainable object), Ma-Blaschko tree kernel (subsumed by gated-sentinel + SAAS), HyperMapper off-the-shelf (missing SAAS), pure SAASBO (bad categoricals), BOCS (binary-only), GFlowNets (need 10⁵+ evals), Hearthstone MESB (no phenotype→genotype map), silent rule-based small-slot fills (rejected — smalls are opponent-conditional vs missile boats / carriers).

### Phase 7.5 — Infrastructure & Reproducibility (planned, scheduled after Phase 7 ships)

Horizontal to algorithmic phases — collapses the 16-step tribal-knowledge bootstrap sequence into a one-command fork-and-go workflow. Full design + alternative ranking: [`docs/reference/phase7.5-infrastructure-reproducibility.md`](docs/reference/phase7.5-infrastructure-reproducibility.md).

Four incremental tiers:
- **(A)** `just` CLI + worker Dockerfile + `starsector-repro check` preflight + Terraform module for static AWS infra + launcher OCR fallback (tesseract-based "find Play Starsector text" replacing the brittle geometric heuristic; drift-proof against minor-version layout shifts). Preserves current architecture, eliminates most AMI rebakes triggered by Python-only changes.
- **(B)** Flyte/Prefect flow replacing `launch_campaign.sh` + bash `trap EXIT` + Tailscale Terraform provider (declares tagOwners, grants, authkey, **and the operator-SSH ACL fragment** so `tailscale ssh` works on first smoke without manual admin-panel editing) + structured `gate.json`.
- **(C)** Ray Tune adoption replacing `StagedEvaluator` + `CloudWorkerPool` + `worker_agent.py` main loop + reliable-queue plumbing — best pursued AS Phase 7's execution-substrate delivery vehicle.
- **(D)** Nix flake + published `REPRODUCE.md` with canonical reference run for ecological-reproducibility validation.

**Success criterion**: forked engineer on unfamiliar laptop reaches passing Tier-2 smoke in under 30 minutes given only the repo URL and a pointer to a legally obtained Starsector copy.

**Rejected alternatives**: Kubernetes/KubeRay (too heavy at <$10k/mo), Airflow/Dagster (DAG-of-different-tasks-oriented), W&B Launch / SageMaker Pipelines (vendor lock-in), Snakemake/Nextflow (bio idioms).

## Commands

- Run Python tests: `uv run pytest tests/ -v`
- Run single test file: `uv run pytest tests/test_parser.py -v`
- Run single test: `uv run pytest tests/test_models.py::test_weapon_sustained_dps -v`
- Build combat harness: `cd combat-harness && JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew jar`
- Run Java tests: `cd combat-harness && JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew test`
- Deploy mod: `cd combat-harness && JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew deploy`
- `STARSECTOR_JDK_HOME` resolves to a JDK 17 (matching Starsector's bundled JRE). Examples: macOS Homebrew → `/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`; Linux → `/usr/lib/jvm/java-17-openjdk` (or 26+ — Gradle 9.4 tolerates higher build hosts as long as `targetCompatibility = 17`).
- Run optimizer (heuristic-only): `uv run python scripts/run_optimizer.py --hull eagle --game-dir game/starsector --heuristic-only`
- Game data location: `game/starsector/data/` (gitignored, not in repo)
- See [`combat-harness/CLAUDE.md`](combat-harness/CLAUDE.md) for Java-specific instructions

## Running the live optimizer (sim mode)

Each Starsector JVM consumes ~2.5 cores under active combat. Over-subscribing CPU causes load-thrash that slows throughput to a crawl. Pick instance count, launch, and stop as follows:

**Sizing (enforced)**: `--num-instances ≤ os.cpu_count() // 3`. `LocalInstancePool.setup()` preflights this and raises `InstanceError` otherwise. On a 12-core host, max is 4; on 9-core, 3.

**Launch**:
```
uv run python scripts/run_optimizer.py --hull <id> --game-dir game/starsector \
    --num-instances <≤nproc/2> --sim-budget <N> --study-db data/<id>.db
```

**Stop — three options in preference order**:
1. **Ctrl-C (preferred)** — `run_optimizer.py` installs SIGINT/SIGTERM/SIGHUP handlers that raise `KeyboardInterrupt`, unwinding `with LocalInstancePool(...)` → `teardown()` writes shutdown signals and terminates JVMs + Xvfb cleanly. `kill <pid>` on the Python orchestrator works the same.
2. **`uv run python scripts/stop_optimizer.py`** — panic button when the orchestrator is gone (crash, `kill -9`, tmux session lost). Writes shutdown signals to every work dir, then SIGTERM → wait → SIGKILL on `StarfarerLauncher` JVMs and `Xvfb :1XX` processes.
3. **Never `pkill -f starsector`** — the JVM cmdline contains `StarfarerLauncher` / `com.fs.starfarer`, not the literal string `starsector`. The correct patterns are `StarfarerLauncher` (JVMs) and `Xvfb :1\d\d` (displays 100–199).

## Workstation OS support

`LocalInstancePool` is **Linux-only** by design — it depends on Xvfb (X11 virtual framebuffer) and xdotool, neither of which has a working macOS or Windows equivalent for headless LWJGL/OpenGL. Any path that provisions local game JVMs has the same constraint: `LocalInstancePool` itself, `scripts/update_manifest.py` (manifest regen, which probes the live game), and `worker_agent.py` (cloud-only AMI).

**Non-Linux orchestrators (macOS, Windows) run cloud-only**: heuristic-only optimizer, the Gradle build of `combat-harness`, the full `pytest` suite, and `CampaignManager`-driven cloud campaigns all work on macOS/Windows. Live local sim does not — use Phase 6 cloud workers (`--worker-pool cloud`) instead. **Don't propose porting `LocalInstancePool` to macOS** — researched 2026-05-08 and rejected as 2–4 weeks of experimental work with low confidence; design intent is that local-Linux and cloud-Linux share one code path, and macOS workstations delegate sim to cloud workers.

`preflight_check` (`optimizer.py`) no-ops the `shutil.which("Xvfb")` / `shutil.which("xdotool")` PATH check when `sys.platform != "linux"`. If anyone tries to actually launch `LocalInstancePool` on a non-Linux host, `setup()` will fail with a clearer downstream error from the Xvfb subprocess.

**Staging `game/starsector/` is platform-agnostic**: the Linux Starsector distribution (`starsector_linux-*.zip`, ~244 MB; download via Backblaze CDN URL from the official release-notes post) extracted into `game/starsector/` is the canonical layout — its `data/`, `mods/`, `starfarer.api.jar`, etc. are byte-identical to what the cloud-worker AMI expects, and Mac/Windows orchestrators only consume `data/` (CSVs/JSONs), the four root JARs (compile classpath), and `mods/` (deploy target). The bundled `jre_linux/` is unused on non-Linux hosts but harmless. `mods/enabled_mods.json` must list `combat_harness` for `preflight_check` to pass.

## Workflow Gates

For every module: spec first ([`docs/specs/`](docs/specs/)), then tests, then implementation. The four skills in [`.claude/skills/`](.claude/skills/) enforce quality at each gate:

| Gate | When | Skill |
|------|------|-------|
| **Planning** | Before any non-trivial implementation | Enter plan mode. Follow `ddd-tdd` lifecycle. |
| **Plan review** | Before calling ExitPlanMode | Run the `plan-review` checklist: self-review + 3 parallel audit sub-agents. |
| **Implementation** | During coding | Follow `ddd-tdd` step 3: one concern per change, verify after each module. |
| **Post-implementation** | After all implementation tasks complete | Run the `post-impl-audit` checklist: mechanical checks + 3 parallel audit sub-agents. |
| **Invariant check** | When reviewing any code change | Reference `design-invariants` for the full checklist. |

For Starsector Java modding specifics (sandbox, file I/O, Janino, combat plugin patterns), see [`.claude/skills/starsector-modding.md`](.claude/skills/starsector-modding.md).

## Documentation conventions

Documentation is organized into six categories under [docs/CONVENTIONS.md](docs/CONVENTIONS.md):
- **specs** ([docs/specs/](docs/specs/)) — module / protocol contracts.
- **reference** ([docs/reference/](docs/reference/)) — design rationale, research, theory.
- **reports** ([docs/reports/](docs/reports/)) — dated empirical evidence.
- **skills** ([.claude/skills/](.claude/skills/)) — procedural how-to / SOP.
- **always-loaded** (this file, [combat-harness/CLAUDE.md](combat-harness/CLAUDE.md), [docs/CONVENTIONS.md](docs/CONVENTIONS.md)) — cross-cutting context.
- **indices** ([docs/project-overview.md](docs/project-overview.md), [docs/reports/INDEX.md](docs/reports/INDEX.md), [experiments/INDEX.md](experiments/INDEX.md), [docs/specs/README.md](docs/specs/README.md)) — navigation.

**Empirical-numbers rule**: specs and references contain NO inline empirical numbers; reports own all dated measurements. See [docs/CONVENTIONS.md](docs/CONVENTIONS.md) §"The empirical-numbers rule".

## Engineering Principles

Global invariants — they apply to all work in this project, regardless of phase, task, or operating mode.

1. **Principled over expedient.** When a principled approach and an expedient shortcut both produce a working result, take the principled one. Concrete shortcuts to refuse: hardcoding a value to dodge a config refactor, duplicating logic instead of extracting an abstraction the codebase already implies, weakening an invariant or test to make a single failure go away, writing a narrow patch when the same bug exists elsewhere, choosing the convenient phrasing of a fix when the correct phrasing requires a slightly larger diff. If the principled fix is genuinely larger than the immediate task, name the trade-off explicitly to the user (minimal fix + principled fix + reason for the gap) and ask which to take — silently picking the small one is the failure mode this rule exists to prevent.

2. **Address issues, don't paper over them.** When you observe a problem — a flaky test, dormant code, a known-wrong assertion, an inconsistency between two files, a load-bearing TODO, a deprecated API call still in use, a comment that contradicts the code, a skipped test, a swallowed exception — fix the root cause in the same change. Do **not**: defer with a new TODO, document the issue in a doc and move on, add a skip/ignore/suppress to bypass it, write a comment explaining why it's broken, or treat "want me to also fix X?" as a way of postponing it. **Deferral requires explicit user consent — it is never the default.** If a principled fix is genuinely out of scope (different subsystem, would balloon the change), surface it explicitly with a proposed fix and ask before deferring. This applies to issues unrelated to the stated task if they're in code already being read or modified — boy-scout rule, mandatory not optional.

These two rules apply equally in `auto` mode: autonomy means "act without asking", not "take the easy path without asking".

## Design Principles

1. **Manifest-as-oracle for game knowledge.** All hullmod applicability, conditional exclusions, and damage multipliers come from `game/starsector/manifest.json` (written by Java `ManifestDumper`, read by Python `GameManifest.load()`). Never add hardcoded hullmod logic in scorer, repair, or search_space — regenerate the manifest instead. See [`docs/specs/29-game-manifest.md`](docs/specs/29-game-manifest.md).

2. **Immutable domain models.** `Build`, `EffectiveStats`, `ScorerResult`, `CombatFitnessConfig`, `ImportanceResult` are frozen dataclasses. Repair returns new instances. `Build.hullmods` is `frozenset`.

3. **Optimizer-space vs domain-space boundary.** Raw optimizer proposals (potentially infeasible) go through `repair_build()` to produce valid `Build` objects. Everything downstream of repair works with concrete, valid Builds.

4. **Data-driven over logic-driven.** Hullmod applicability and exclusions are a manifest-driven registry, not scattered if-else chains. Adding a new hullmod = regen the manifest.

5. **Forward compatibility — warn, don't crash.** Unknown enum values from future game versions: `from_str()` returns `None`, parser logs warning and skips the record. Never crash on unknown game data.

6. **Structured scorer output.** `heuristic_score()` returns `ScorerResult` with all component metrics. These become Phase 7 behavior descriptors and Phase 8 features without refactoring.

7. **Verify game facts against actual game files, never assume.** The game data files at `game/starsector/data/` and `manifest.json` are the ground truth. Specific pitfalls: hullmod IDs are non-obvious (check `hull_mods.csv`), `weapon_data.csv` `type` is damage type not weapon type, `ship_data.csv` `designation` is a role string not hull size. See [`.claude/skills/starsector-modding.md`](.claude/skills/starsector-modding.md) for the full list.

## Design Invariants

- Every `Build` returned by `repair_build()` passes `is_feasible()`.
- `compute_effective_stats()` is the ONLY function that applies hullmod stat modifications.
- The manifest (`GameManifest.load()`) is the ONLY source of hullmod applicability, conditional exclusions, and damage multipliers. No hardcoded game-rule registries.
- All game constants (MAX_VENTS, damage multipliers, etc.) come from `manifest.constants`, not scattered literals.
- **No magic numbers in function bodies.** Timeouts, coordinates, polling intervals, thresholds, and batch sizes must live in config dataclasses (`InstanceConfig`, `OptimizerConfig`, `CombatFitnessConfig`) — never as literals in function bodies.

For the full mechanical checklist with runnable grep commands, see [`.claude/skills/design-invariants.md`](.claude/skills/design-invariants.md).

## Project Layout

```
src/starsector_optimizer/          # Python modules
├── models.py                      # Dataclasses + enums (ShipHull, Weapon, Build, BuildSpec, CombatFitnessConfig, TWFEConfig, EBShrinkageConfig, ShapeConfig, RegimeConfig, CampaignConfig, StudyConfig, WorkerConfig, CostLedgerEntry, GlobalAutoStopConfig, EngineStats, etc.)
├── game_manifest.py               # GameManifest.load() — manifest-as-oracle accessor (replaces hullmod_effects.py)
├── parser.py                      # CSV + loose JSON → model objects
├── search_space.py                # Per-hull weapon/hullmod compatibility (sourced from manifest)
├── repair.py                      # Constraint enforcement (optimizer → domain boundary)
├── scorer.py                      # Heuristic scoring → ScorerResult
├── variant.py                     # Build ↔ .variant JSON / BuildSpec
├── calibration.py                 # Random build generation + feature extraction
├── estimator.py                   # Throughput + cost estimation for simulation campaigns
├── result_parser.py               # Parse combat result JSON ↔ Python dataclasses
├── evaluator_pool.py              # EvaluatorPool ABC: cross-backend contract for matchup dispatch
├── instance_manager.py            # LocalInstancePool — N parallel local Starsector game instances
├── cloud_provider.py              # CloudProvider ABC + AWSProvider (boto3) + HetznerProvider stub
├── cloud_worker_pool.py           # CloudWorkerPool — Redis reliable-queue + Flask /result listener
├── cloud_runner.py                # Per-study subprocess entry point (provision_fleet → run study → terminate_fleet)
├── cloud_userdata.py              # render_user_data — UserData script generation
├── worker_agent.py                # Runs on cloud VM: Redis pull → LocalInstancePool → HTTP POST
├── campaign.py                    # CampaignManager + CostLedger; subprocess-per-study supervisor
├── combat_fitness.py              # Hierarchical composite combat fitness score
├── opponent_pool.py               # Diverse opponent pool per hull size
├── deconfounding.py               # TWFE decomposition + EB shrinkage + triple-goal rank correction
├── importance.py                  # Parameter importance analysis (fANOVA) + fixed params
└── optimizer.py                   # Optuna integration, ask-tell loop, warm-start

combat-harness/                    # Java combat harness mod
├── CLAUDE.md                      # Java-specific instructions
├── build.gradle.kts               # Gradle build
├── src/main/java/starsector/combatharness/
│   ├── MatchupConfig.java         # Single matchup config POJO + BuildSpec inner class
│   ├── MatchupQueue.java          # Batch queue — reads JSON array from saves/common/
│   ├── VariantBuilder.java        # Programmatic ShipVariantAPI construction from BuildSpec
│   ├── DamageTracker.java         # DamageListener — per-ship damage accumulation
│   ├── ResultWriter.java          # Batch results + done signal via SettingsAPI
│   ├── CombatHarnessPlugin.java   # State machine: INIT → SETUP → FIGHTING → DONE → WAITING
│   ├── CombatHarnessModPlugin.java # BaseModPlugin — mod entry point
│   ├── TitleScreenPlugin.java     # Auto-navigates to mission on title screen
│   ├── MenuNavigator.java         # java.awt.Robot menu clicking (1920x1080 calibrated)
│   ├── ManifestDumper.java        # PROBE_ITERATE — writes game/starsector/manifest.json (Phase-7-prep refactor)
│   └── AttackAdmiralAI.java       # Pinned-aggressive admiral AI for sim matchups
├── src/main/java/data/missions/optimizer_arena/
│   └── MissionDefinition.java     # Mission setup (V2 loadout: addToFleet placeholder + member.setVariant pre-deploy)
└── mod/                           # Deployed to game/starsector/mods/combat-harness/
    └── mod_info.json

# I/O paths (game appends .data to all saves/common/ filenames):
#   Input:  saves/common/combat_harness_queue.json.data
#   Output: saves/common/combat_harness_results.json.data
#   Done:   saves/common/combat_harness_done.data
#   Health: saves/common/combat_harness_heartbeat.txt.data

docs/
├── CONVENTIONS.md                 # Documentation system: categories, frontmatter, empirical-numbers rule
├── project-overview.md            # Phase-grouped tour
├── specs/                         # Module / protocol contracts (see specs/README.md for the number registry)
├── reference/                     # Design rationale, research, theory
└── reports/                       # Dated empirical evidence (see reports/INDEX.md)

experiments/
└── INDEX.md                       # Forward-looking experiment registry; pre-V2 dirs deleted 2026-05-10

.claude/skills/                    # Quality-gate + ops skills
├── ddd-tdd.md                     # Spec → test → impl → verify lifecycle
├── design-invariants.md           # Full invariant checklist with mechanical checks
├── plan-review.md                 # Pre-approval review (self-review + 3 audit agents)
├── post-impl-audit.md             # Post-implementation verification (checks + 3 audit agents)
├── cloud-worker-ops.md            # Cloud campaign SOP
└── starsector-modding.md          # Java modding pitfalls (sandbox, file I/O, Janino, etc.)
```
