---
type: always-loaded
status: shipped
last-validated: 2026-05-10
---

# Combat Harness Mod

Java mod for Starsector 0.98a that runs automated AI-vs-AI combat and exports results as JSON. The deployed ship loadout uses the V2 placeholder-then-swap path (`addToFleet` stock variant → `FleetMember.setVariant` pre-deployment); the V1 mid-combat `variant.clear()` + `addWeapon()` approach mutated the variant data structure but failed to propagate to the deployed `WeaponAPI` instances. Master invalidation report: [../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md).

## Commands

- Build: `JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew jar`
- Test: `JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew test`
- Deploy to game: `JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew deploy`
- Build + test + deploy: `JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew clean jar test deploy`
- Launch game with mod (Linux only): `cd ../game/starsector && ./starsector.sh`
- `STARSECTOR_JDK_HOME` = JDK 17 (matching the bundled JRE). macOS Homebrew: `/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`. Linux: `/usr/lib/jvm/java-17-openjdk` (Gradle 9.4 tolerates higher build hosts).
- **Build gates** (every `gradlew jar`): `-Xlint:all -Werror` + Error Prone + NullAway-as-error. Error Prone is **pinned at 2.42.0** — 2.43.0+ ship JDK-21 class files and crash the JDK-17 compiler; do not bump without moving the build to a JDK 21 toolchain with `options.release = 17` (a deliberate convention change, not a version bump). `EmptyCatch` is disabled (deliberate `catch (Throwable ignored)` wrappers around obfuscated game API); NullAway enforcement is scoped to `starsector.combatharness` + `data.missions` (the game API is unannotated and treated optimistically — nulls returned by game API calls are NOT checked; keep the defensive-catch pattern there). Evidence: [2026-07-12 tooling research](../docs/reports/2026-07-12-quality-tooling-research.md).

## Architecture (one matchup per mission cycle)

1. Python writes `combat_harness_queue.json.data` to `saves/common/` (1 matchup config).
2. Game launches → `TitleScreenPlugin` detects queue → `MenuNavigator` auto-navigates to Optimizer Arena (the `triggered` flag resets when `GameState != TITLE` so persistent session reuse works).
3. `MissionDefinition` deploys each player ship via the **V2 placeholder-then-swap pattern**: `addToFleet(side, stockVariantId, ...)` returns a `FleetMemberAPI`, then `member.setVariant(VariantBuilder.createVariant(spec), false, true)` swaps in the optimizer-generated variant before the deployment screen processes the fleet. Enemy ships use stock variant IDs via `addToFleet()`.
4. `CombatHarnessPlugin.doSetup` filters `engine.getShips()` by `spec.variantId` (cross-trial state-leak guard — see [../docs/reports/2026-05-09-wave0-validation.md](../docs/reports/2026-05-09-wave0-validation.md) §3.4) and defers state transition until all expected ships are visible (`SETUP_VARIANT_WAIT_FRAMES = 600` backstop, with `[V2_SETUP_DEFER]` / `[V2_SETUP_TIMEOUT]` logs). On match it sets CR live on each deployed `ShipAPI` (`setCurrentCR(cr)` + `setCRAtDeployment(cr)` + `setRetreating(false, false)`) — `getCurrentCR()` does NOT inherit from the FleetMember's repair tracker, and `CR=0` triggers auto-retreat. `LoadoutDiagnostic` validates the deployed loadout against the spec once at end of SETUP as a permanent canary; the matching `[V2_SETUP_VARIANT]` log records the live variant id + first-3 physical weapon ids per ship.
5. Plugin state machine: INIT → SETUP → FIGHTING → DONE → WAITING.
6. After matchup: `ResultWriter` writes results + done signal, Robot dismiss thread launched, **then** `endCombat()` called. The order matters — the engine stops calling `advance()` immediately after `endCombat()`, so any post-combat work must launch first.
7. `TitleScreenPlugin` detects new queue → fresh mission cycle.

**Why single-matchup-per-mission**: `spawnFleetMember()` mid-combat causes ships to retreat via `directRetreat=true` set below the public API. `addFleetMember(side, member)` with a pre-built FleetMember has the same issue. Only `addToFleet()`-then-`setVariant` produces ships with proper AI behavior. Full bug catalog + rejected workarounds: [`starsector-modding`](../.claude/skills/starsector-modding.md) §"Ship Spawning — `spawnFleetMember()` Retreat Bug".

## Design invariants

- `combat_harness_queue.json` is the ONLY input; `combat_harness_results.json` is the ONLY output; `combat_harness_done` is the completion signal Python polls for.
- The plugin never modifies combat (no damage modification, no custom AI).
- One matchup per mission — `endCombat()` + Robot dismiss + `TitleScreenPlugin` restart between matchups.
- All config values have sane defaults (`time_mult=5` engine ceiling, `time_limit=300`, map `24000×18000`).
- `MissionDefinition` gracefully handles missing queue (shows error in briefing).

## Pointers

- File protocol (queue / results / done / heartbeat / shutdown), JSON schemas, lifecycle: [spec 09](../docs/specs/09-combat-protocol.md).
- Combat plugin contract (state machine, end-of-match ordering): [spec 13](../docs/specs/13-combat-harness-plugin.md).
- Programmatic variant construction: [spec 27](../docs/specs/27-variant-builder.md).
- Manifest-dumper probe-mode mission: [spec 29](../docs/specs/29-game-manifest.md).
- Sandbox / file I/O / Janino / API caveats / Robot UI automation / Xvfb requirements / `MenuNavigator` coordinates: [`starsector-modding`](../.claude/skills/starsector-modding.md).
