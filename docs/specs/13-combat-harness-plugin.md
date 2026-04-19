# Combat Harness Plugin Specification

Single-matchup-per-mission state machine that runs one AI-vs-AI combat, writes results, and ends the mission. Defined in `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`.

Extends `BaseEveryFrameCombatPlugin`. Attached by `MissionDefinition` via `api.addPlugin()`.

## State Machine

```
INIT → SETUP → FIGHTING → DONE → WAITING → (shutdown/timeout → exit)
```

One matchup per mission. After `endCombat()`, Robot dismisses results, game returns to title screen, TitleScreenPlugin detects new queue and auto-navigates to a fresh mission.

### State: INIT (first advance() call)
1. Load queue via `MatchupQueue.loadFromCommon()`
2. `engine.setDoNotEndCombat(true)` — prevent auto-end during setup
3. Transition to SETUP

### State: SETUP
1. Get `queue.get(0)` → `currentConfig` (single matchup per mission)
2. Apply time multiplier: `engine.getTimeMult().modifyMult("harness", config.timeMult)`
3. Create new DamageTracker, register via `engine.getListenerManager().addListener(tracker)`
4. Collect ships deployed by MissionDefinition (via `addToFleet()` — proper CR/AI behavior):
   - Iterate `engine.getShips()`, skip fighters
   - Owner 0 → playerShips, Owner 1 → enemyShips
5. Swap player ship loadout to real build spec:
   - `variant.clear()` + `addWeapon()`/`addMod()` for each weapon/hullmod
   - `setNumFluxVents()`, `setNumFluxCapacitors()`, `autoGenerateWeaponGroups()`
   - `ship.setCurrentCR(spec.cr)`, `ship.setCRAtDeployment(spec.cr)`
6. **Engine-computed SETUP stats read (6 fields):** After loadout
   swap, read the player ship's effective post-hullmod stats via
   the engine's authoritative `MutableShipStats` accessors and
   cache on plugin fields for emission in the result JSON:

   **Original 3 (Phase 5D, 2026-04-18):**
   - `currentEffMaxFlux = ship.getMutableStats().getFluxCapacity().getModifiedValue()`
   - `currentEffFluxDissipation = ship.getMutableStats().getFluxDissipation().getModifiedValue()`
   - `currentEffArmorRating = ship.getMutableStats().getArmorBonus().computeEffective(ship.getHullSpec().getArmorRating())`

   **Added 3 (Phase-7-prep, 2026-04-19):**
   - `currentEffHullHpPct = ship.getMutableStats().getHullBonus().computeEffective(hullSpec.getHitpoints()) / hullSpec.getHitpoints()`
     — ratio form (not raw HP) so variance is hull-size-invariant.
     Bimodal/trimodal driven by Reinforced Bulkheads (+40%) and
     Blast Doors (+20%).
   - `currentBallisticRangeBonus = ship.getMutableStats().getBallisticWeaponRangeBonus().computeEffective(1000f)`
     — multiplicative bonus against a 1000-range baseline; driven by
     Integrated Targeting Unit (+10/20/40/60% by hull size), Dedicated
     Targeting Core (0/0/15/25%), Unstable Injector (×0.85).
   - `currentShieldDamageTakenMult = ship.getMutableStats().getShieldDamageTakenMult().getModifiedValue()`
     — driven by Hardened Shields (-20/25%) and S-modded Front Shield
     Emitter. Stabilized Shields does NOT feed this (it modifies
     `ShieldUpkeepMult`).

   Null-check `getMutableStats()` and `getHullSpec()` — any null path
   stores `Float.NaN` (always-emit policy; parser handles NaN as
   malformed).

   **Why not read via `ArmorGridAPI`?** `ArmorGridAPI` cells reflect
   current damage state, not rated armor. `StatBonus.computeEffective(base)`
   is the canonical accessor because it applies flat + percent + mult
   bonuses to the base hull-spec rating.

   **API verified 2026-04-18** (original 3) **and 2026-04-19**
   (new 3 against vanilla 0.98a-RC8; see
   `docs/reference/phase5d-covariate-adjustment.md` §5 + the
   Phase-7-prep refactor plan).
7. Record `spawnTime`. Set `contactMade = false`.
8. Transition to FIGHTING

### State: FIGHTING
Per-frame:
1. **Camera:** Center viewport on midpoint of all tracked ships via `ViewportAPI.setExternalControl(true)` + `viewport.set()`.
2. **Heartbeat** every `HEARTBEAT_INTERVAL_FRAMES` (60) frames — enriched format with HP fractions and alive counts.
3. **Contact detection:** If `!contactMade`:
   - If `engine.isFleetsInContact()` → start combat timer (`matchupStartTime = now`), log contact
   - Else if `(now - spawnTime) > MAX_APPROACH_TIME (30s)` → force combat timer start (approach timeout for evasive AI)
4. **Custom win detection:** Count alive non-fighter ships per side from tracked lists. If one side has zero → other side wins. If both zero → TIMEOUT.
5. **Timeout check:** If `contactMade` and `(now - matchupStartTime) > timeLimitSeconds` → TIMEOUT
6. On end:
   - Build result via `ResultWriter.buildMatchupResult(..., currentEffMaxFlux, currentEffFluxDissipation, currentEffArmorRating, currentEffHullHpPct, currentBallisticRangeBonus, currentShieldDamageTakenMult)` — six trailing float args are the engine-computed SETUP stats from step SETUP.6
   - Write results array + done signal via `ResultWriter.writeAllResults()` + `ResultWriter.writeDoneSignal()`
   - Unregister DamageTracker
   - **Launch Robot dismiss thread** (must happen before `endCombat()` — see note below)
   - `engine.setDoNotEndCombat(false)` then `engine.endCombat(0f, winnerSide)`
7. Transition to DONE

**Timer logic:** The time limit only counts combat time, not approach time. Ships may take several seconds to fly toward each other after spawning. If one side is evasive and never engages, the 30-second approach timeout forces the combat timer to start anyway.

**Critical: Robot thread launch timing.** The Robot dismiss thread must be launched in the same frame as `endCombat()`, before the call. After `endCombat()`, the engine stops calling `advance()` almost immediately — if Robot launch is deferred to a later frame (e.g., in DONE state), it will never execute, leaving the game stuck on the mission results screen.

### State: DONE
Fallback transition — immediately moves to WAITING. Robot thread was already launched in FIGHTING.

### State: WAITING

Error recovery state. After `endCombat()`, the engine typically stops calling `advance()` within a few frames. WAITING provides shutdown signal handling and idle timeout in case Robot fails to dismiss results or the engine continues calling `advance()` unexpectedly.

Per-frame (while engine still calls `advance()`):
1. **Heartbeat** every `HEARTBEAT_INTERVAL_FRAMES` — zeros (0 HP, 0 alive) since no ships exist
2. **Shutdown signal check**: If `fileExistsInCommon("combat_harness_shutdown")` → delete signal, `System.exit(0)`
3. **Timeout**: Increment `waitingFrameCount`. If `> WAITING_TIMEOUT_FRAMES (3600, ~60s at 60fps)` → `System.exit(0)` for clean shutdown

**Constants:**
```java
private static final String SHUTDOWN_FILE = MatchupConfig.COMMON_PREFIX + "shutdown";
private static final int WAITING_TIMEOUT_FRAMES = 3600;
private static final int HEARTBEAT_INTERVAL_FRAMES = 60;
private static final float MAX_APPROACH_TIME = 30f;
```

## Custom Win Detection

```java
private int countAlive(List<ShipAPI> ships) {
    int count = 0;
    for (ShipAPI s : ships) {
        if (s.isAlive() && !s.isFighter()) count++;
    }
    return count;
}
```

With `setDoNotEndCombat(true)`, `engine.isCombatOver()` stays false. We detect matchup end ourselves.

## Error Handling

- Queue load failure (INIT) → log error, `System.exit(1)`
- Result write failure → log error, continue (best effort)
- Signal file deletion failure → log warning, continue (best effort)
- Always null-check engine in `advance()`

## Simulation fidelity floors

Three in-scope simplifications the harness makes deliberately —
documented so downstream analysis does not mistake them for bugs and
Phase 7's self-correcting mixture can absorb the resulting evidence.

1. **Weapon groups are engine-generated.** After `variant.clear()` +
   `addWeapon()`/`addMod()` in SETUP.5, the harness calls
   `variant.autoGenerateWeaponGroups()` (`VariantBuilder.java:48`;
   same call in `CombatHarnessPlugin` during loadout swap).
   `BuildSpec` carries no group metadata, so the Python side cannot
   specify `autofire` (off for ammo-limited missiles),
   `mode = ALTERNATING` (expensive ballistics), or cross-weapon
   grouping (flux-balanced firing patterns). Stock `.variant` files
   carry hand-tuned `weaponGroups` that the auto-generator does not
   reproduce. AI flux-vs-benefit evaluation is per-group, so
   misgrouping materially changes combat behaviour.
2. **No fighter wings.** `BuildSpec` has no wing field;
   `VariantBuilder` never calls `variant.setWing(...)` /
   `addWing(...)`; `ManifestDumper` does not enumerate
   `FighterWingSpecAPI` / `wing_data.csv`. Carrier hulls deploy with
   empty bays. See spec 04 and spec 29.
3. **No officer skills.** Combat-harness ships deploy without
   `PersonAPI` / `OfficerDataAPI` population — default personality,
   zero skills, no Target Analysis / Shield Modulation /
   Helmsmanship bonuses. Already accounted for in
   `docs/reference/phase7-search-space-compression.md` §2.10 as
   "default-personality, un-officered"; the self-correcting mixture
   downweights skill-dependent archetypes (SO brawler, kinetic
   brawler wanting Target Analysis) whose predicted fitness
   overshoots the simulation.

## Probe mode — ManifestDumper (schema v2, Commit G)

In addition to combat simulation, the harness supports a **manifest
probe mode** triggered by a sentinel file
`combat_harness_manifest_request` in `saves/common/`. The probe runs
inside the `optimizer_arena` mission (not a separate mission) because
`HullModEffect.isApplicableToShip(ship)` requires a live `ShipAPI`,
which only exists post-combat-init. A minimal stub (wolf + lasher)
is deployed so the engine passes the single-sided refusal gate; the
plugin then drives hull spawning itself.

**State machine extension:**
```
INIT → (sentinel detected) → PROBE_WAIT → PROBE_ITERATE (BASE → CONDITIONAL)
     → finishAndExit → System.exit(0)
INIT → (no sentinel)         → SETUP → FIGHTING → DONE → WAITING (combat path)
```

### State: PROBE_WAIT
1. `advance()` bypasses the normal pause check for PROBE_WAIT /
   PROBE_ITERATE states — the engine otherwise keeps combat paused
   on the deployment screen.
2. On frame 1, call `engine.setPaused(false)` to force-unpause.
3. Wait up to `PROBE_WAIT_MAX_FRAMES` (300) for both the player
   stub and the enemy stub to finish deploying past the engine's
   single-sided refusal. On success, seed a deterministic iterator
   over `Global.getSettings().getAllShipHullSpecs()` sorted by id
   and transition to PROBE_ITERATE, phase=BASE.

### State: PROBE_ITERATE

Two phases share the same state:

**Phase BASE (per-hull standalone applicability + determinism canary).**
Per frame, pull up to `HULLS_PER_FRAME_BASE = 10` hulls from the
iterator. Skip-filter drops `HullSize.FIGHTER`, `HullSize.DEFAULT`,
and hulls with `ShipTypeHints` in `{STATION, MODULE,
SHIP_WITH_MODULES, HIDE_IN_CODEX}`. For each accepted hull:
1. `createEmptyVariant(hullId + "_probe_base_" + idx, hullSpec)` —
   empty variant inherits the hull's built-in mods via `hasHullMod`
   / `getHullMods` dispatch (per `ShipVariantAPI.java:30-33`).
2. `createFleetMember` → `spawnFleetMember` off-map at
   `(PROBE_OFFMAP_X = -50000f, y = idx * PROBE_Y_SPACING)`.
3. For every `HullModSpecAPI m`, call `m.getEffect()
   .isApplicableToShip(ship)` **twice**. Add `m.getId()` to
   `applicableByHull[hullId]` if the first call returns true.
   Record `m.getId()` in `statefulMods` if the two calls diverge
   (non-deterministic probe). `statefulMods` is emitted into
   `manifest.constants.stateful_hullmods`; non-empty fails
   `tests/test_game_manifest.py::test_no_stateful_hullmods`.
4. `engine.removeEntity(ship)` + `fm.removeDeployed(ship, false)`
   (per `CombatEngineAPI.java:63`; this is the documented despawn
   API — `setHitpoints(0f)` leaves the ship in a death-animation
   state and retains the collision-grid entry).

When the iterator is drained, phase transitions to CONDITIONAL.

**Phase CONDITIONAL (pairwise conflict graph per hull).**
Iterate every hull recorded in `applicableByHull` in sorted order.
Per frame, pull `HULLS_PER_FRAME_CONDITIONAL = 1` hull (pairwise
probing is ~100² calls per hull vs ~129 for the BASE probe — one
hull per frame keeps advance() responsive). For each hull:
1. Spawn a **fresh** probe ship (separate variant, separate spawn
   call — no cross-talk with BASE's determinism probe ships).
2. For each mod `A ∈ applicableByHull[hullId]`:
   - `variant.addMod(A)`.
   - For each mod `B ∈ applicableByHull[hullId] \ {A}`:
     - If `B.getEffect().isApplicableToShip(ship) == false`, record
       `B` in `condExclByHull[hullId][A]`.
   - `variant.removeMod(A)` between iterations.
3. Despawn the probe ship.

Records the directional exclusion graph
`hulls[H].conditional_exclusions[A] = {B that drop when A is
installed}`. Python collapses to undirected edges at repair time
(`search_space.py::_collect_incompatible_pairs`).

When the iterator is drained, call `finishAndExit`.

### finishAndExit

1. Read the git SHA from the classpath resource
   `combat-harness-build-info.properties` (populated by gradle's
   `generateBuildInfo` task — fails loudly if missing).
2. Call `ManifestDumper.dumpToCommon(gameVersion, modCommitSha,
   applicableByHull, condExclByHull, statefulMods)` which writes:
   - `combat_harness_manifest_constants.json.data`
   - `combat_harness_manifest_weapons.json.data`
   - `combat_harness_manifest_hullmods.json.data`
   - `combat_harness_manifest_hulls_NNN.json.data` (multi-part —
     the hulls blob exceeds the ~1 MiB `writeTextFileToCommon` cap
     under schema v2 because `applicable_hullmods` +
     `conditional_exclusions` add ~4 KB/hull).
   - `combat_harness_manifest_done.data` (written LAST — Python
     polls for this and is guaranteed all parts are present).
3. Delete the `combat_harness_manifest_request` sentinel.
4. `engine.endCombat(...)` + `System.exit(0)`.

### MissionDefinition branching

The `optimizer_arena` mission's `defineMission` checks for the
sentinel file:
- If present: add a minimal stub — player-side `wolf`, enemy-side
  `lasher` — via `addFleetMember`. Pre-Commit-G stubs included more
  hulls to seed the old 4-representative probe; schema v2 renders
  that obsolete because the plugin drives per-hull spawning.
- If absent: normal combat branch (stock variants + AI).

### ManifestDumper API

`starsector.combatharness.ManifestDumper` is a static utility class:

```java
public static final int SCHEMA_VERSION = 2;  // per-hull applicability
public static final int MAX_VENTS_PER_SHIP = 30;
public static final int MAX_CAPACITORS_PER_SHIP = 30;
public static final float DEFAULT_CR = 0.7f;
// Damage multipliers (shield / armor / hull) per DamageType are
// sourced from `DamageType.getShieldMult() / getArmorMult() /
// getHullMult()` at dump time — not hardcoded. Mods that retune
// DamageType at load time surface automatically.

public static void dumpToCommon(
    String gameVersion,
    String modCommitSha,
    Map<String, Set<String>> applicableByHull,
    Map<String, Map<String, Set<String>>> condExclByHull,
    Set<String> statefulMods
) throws JSONException, IOException;
```

Ship skins (`.skin` files in `data/hulls/skins/`) are enumerated as
distinct hull IDs by the engine's
`Global.getSettings().getAllShipHullSpecs()`; their slot / built-in
overrides resolve transparently through `createEmptyVariant`.

### Schema bumping contract

If any of these change, bump `SCHEMA_VERSION` in the Java constants
AND `EXPECTED_SCHEMA_VERSION` in `game_manifest.py`:
- A field is added, removed, or renamed in `WeaponSpec`,
  `HullmodSpec`, `HullManifestEntry`, or `GameConstants`.
- The probe algorithm changes (e.g. new determinism / conditional
  logic).
- The enum vocabulary gains a member that affects existing parse
  paths (new enum members alone are forward-compat — see spec 29).

Version bumps are a single-commit event: Java constant + Python
constant + regenerated manifest + all affected downstream readers.
