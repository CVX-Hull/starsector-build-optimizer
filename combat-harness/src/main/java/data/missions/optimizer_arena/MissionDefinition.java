package data.missions.optimizer_arena;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;
import com.fs.starfarer.api.combat.ShipVariantAPI;
import com.fs.starfarer.api.fleet.FleetGoal;
import com.fs.starfarer.api.fleet.FleetMemberAPI;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.mission.FleetSide;
import com.fs.starfarer.api.mission.MissionDefinitionAPI;
import com.fs.starfarer.api.mission.MissionDefinitionPlugin;

import org.apache.log4j.Logger;

import starsector.combatharness.CombatHarnessPlugin;
import starsector.combatharness.HarnessTraceContext;
import starsector.combatharness.ManifestDumper;
import starsector.combatharness.MatchupConfig;
import starsector.combatharness.MatchupQueue;
import starsector.combatharness.VariantBuilder;

/**
 * Sets up the first matchup's ships and attaches the CombatHarnessPlugin.
 * The plugin handles subsequent matchups by spawning/removing ships mid-combat.
 *
 * The first matchup's ships MUST be added here (via addToFleet/addFleetMember) because
 * the game shows a "No ships deployed" screen for empty fleets and won't start combat.
 * spawnShipOrWing()/spawnFleetMember() only work once combat is already running.
 */
public class MissionDefinition implements MissionDefinitionPlugin {

    private static final Logger log = Logger.getLogger(MissionDefinition.class);

    public void defineMission(MissionDefinitionAPI api) {
        log.info("optimizer_arena.MissionDefinition: defineMission entered");
        // Manifest-probe branch: triggered by the Python update_manifest.py
        // driver writing combat_harness_manifest_request.data. We need one
        // real ShipAPI per vanilla hull-size bucket (FRIGATE/DESTROYER/CRUISER/
        // CAPITAL_SHIP) so HullModEffect.getUnapplicableReason() can inspect
        // a live ship — the probe cannot run outside a CombatEngine (see
        // ManifestDumper.tryCreateProbeShip comment). The plugin detects
        // probe mode on its own, runs the probe in SETUP, writes the
        // manifest, and exits the process.
        if (Global.getSettings().fileExistsInCommon(
                ManifestDumper.MANIFEST_REQUEST_FILE)) {
            log.info("optimizer_arena.MissionDefinition: probe-mode branch taken");
            // Probe has 4 ships on the player side — with useDefaultAI=true
            // (human controls flagship), Starsector blocks on the deployment
            // screen waiting for the player to deploy 4 ships manually. The
            // normal matchup flow has 1 ship per side which auto-deploys,
            // masking this gotcha. `false` means "AI controls all" → game
            // auto-deploys → combat proceeds → probe can observe ShipAPIs.
            api.initFleet(FleetSide.PLAYER, "PROBE", FleetGoal.ATTACK, false);
            api.initFleet(FleetSide.ENEMY, "DUMMY", FleetGoal.ATTACK, false);
            api.setFleetTagline(FleetSide.PLAYER, "Manifest probe — auto-exits");
            api.setFleetTagline(FleetSide.ENEMY, "Dummy target");

            // Commit G: MissionDefinition spawns the MINIMAL stub needed to
            // get past the single-sided deployment refusal — 1 player + 1
            // enemy. The plugin's PROBE_ITERATE state spawns every probe
            // hull itself via `CombatFleetManagerAPI.spawnFleetMember`,
            // off-map, batched across frames, with explicit
            // `engine.removeEntity` despawn. Reserves-based spawning
            // (the Commit D approach) doesn't scale to ~500 hulls; the
            // stub wolf stays deployed throughout as the anchor that
            // keeps `isCombatOver()` from firing when the probe
            // spawn/despawn sweep transiently empties the deployed
            // fleet list.
            addProbeShipEmptyVariant(api, FleetSide.PLAYER, "wolf");
            addProbeShipEmptyVariant(api, FleetSide.ENEMY, "lasher");

            api.initMap(-8000f, 8000f, -6000f, 6000f);
            api.addPlugin(new CombatHarnessPlugin());
            return;
        }

        if (!MatchupQueue.existsInCommon()) {
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
            api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);
            api.addBriefingItem("ERROR: No queue file found in saves/common/");
            api.addBriefingItem("Write combat_harness_queue.json.data before launching.");
            api.initMap(-8000f, 8000f, -6000f, 6000f);
            return;
        }

        MatchupQueue queue;
        String queueHash;
        try {
            String rawQueue = MatchupQueue.readRawFromCommon();
            queueHash = MatchupQueue.fingerprint(rawQueue);
            queue = MatchupQueue.fromJSON(new org.json.JSONArray(rawQueue));
        } catch (Exception e) {
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
            api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);
            api.addBriefingItem("ERROR: Failed to parse queue: " + e.getMessage());
            api.initMap(-8000f, 8000f, -6000f, 6000f);
            return;
        }

        // Both sides fully AI-controlled with ATTACK goal
        // useDefaultAI=true required — false means "player controls" which causes
        // retreat behavior when no human commander is present
        api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
        api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);

        api.setFleetTagline(FleetSide.PLAYER, "Optimizer Candidate");
        api.setFleetTagline(FleetSide.ENEMY, "Test Opponent");

        // Player ships: add via `addToFleet(stockVariant)`, then immediately
        // `setVariant(customVariant)` on the returned FleetMember so the
        // pre-deployment variant build still propagates weapons + hullmods.
        //
        // Why not `addFleetMember(side, member)` with a pre-built FleetMember?
        // Tested 2026-05-09 (smoke #15-#17 with [SHIP_DUMP] tracing): an
        // `addFleetMember`-deployed ship comes up with `retreat=true` set
        // internally and the AI immediately heads for the deploy point.
        // Setting `setRetreating(false, false)` doesn't override (skill #14
        // documents the same for `spawnFleetMember`). The matchup then ends
        // in <2s with `winner=ENEMY, dur=0, hp_diff=0` — no real combat.
        //
        // `addToFleet(stockVariant, ...)` does NOT trigger this retreat
        // (smoke #11 produced 42.7s real combat with this path). We use the
        // stock variant only as a placeholder to get past the retreat
        // initialization, then immediately `setVariant(...)` on the returned
        // FleetMember — which propagates to the deployed `ShipAPI` because
        // the swap happens BEFORE the deployment screen processes the fleet.
        MatchupConfig first = queue.get(0);
        HarnessTraceContext.startMission(queueHash, first.matchupId);
        log.info("[TRACE_MISSION] " + HarnessTraceContext.summary("<plugin-not-loaded>")
                + " queue_size=" + queue.size());

        for (int i = 0; i < first.playerBuilds.length; i++) {
            MatchupConfig.BuildSpec spec = first.playerBuilds[i];
            try {
                String stockVariant = findAnyVariantForHull(spec.hullId);
                FleetMemberAPI member = api.addToFleet(
                        FleetSide.PLAYER, stockVariant,
                        FleetMemberType.SHIP, stockVariant, false);
                String beforeId = safeVariantId(member);
                ShipVariantAPI customVariant = VariantBuilder.createVariant(spec);
                // setVariant args: (variant, withFighters, force).
                // force=true overrides the existing variant unconditionally.
                member.setVariant(customVariant, false, true);
                member.getRepairTracker().setCR(spec.cr);
                log.info("[V2_DEPLOY] matchup=" + first.matchupId
                        + " mission_uuid=" + HarnessTraceContext.missionUuid()
                        + " mission_queue_hash=" + queueHash
                        + " spec=" + spec.variantId
                        + " placeholder=" + stockVariant
                        + " before_setvariant=" + beforeId
                        + " custom=" + safeVariantHullId(customVariant)
                        + " custom_static_weapons="
                        + staticVariantWeaponMap(customVariant, spec)
                        + " custom_static_hullmods="
                        + staticVariantHullmods(customVariant)
                        + " custom_flux=(" + safeFluxVents(customVariant)
                        + "," + safeFluxCaps(customVariant) + ")"
                        + " after_setvariant=" + safeVariantId(member)
                        + " member_id=" + safeMemberId(member));
            } catch (Throwable t) {
                log.error("Failed to build player ship from spec "
                        + spec.variantId + " (hull=" + spec.hullId + ")", t);
                throw t;
            }
        }

        // Enemy ships: use stock variant IDs
        for (int i = 0; i < first.enemyVariants.length; i++) {
            String variantId = first.enemyVariants[i];
            api.addToFleet(FleetSide.ENEMY, variantId,
                    FleetMemberType.SHIP, variantId, false);
        }

        // Arena map
        float hw = 12000f;
        float hh = 9000f;
        api.initMap(-hw, hw, -hh, hh);

        // Attach plugin — handles combat monitoring and subsequent matchups
        api.addPlugin(new CombatHarnessPlugin());
    }

    /**
     * Find any stock variant ID for a given hull ID. Used as a placeholder
     * for `addToFleet` — the variant gets immediately swapped via
     * `setVariant(customVariant)` on the returned FleetMember. The point of
     * the placeholder is to get the engine to register the ship in the
     * non-retreating "main fleet" track; the variant itself is overwritten
     * before deployment.
     */
    private static String findAnyVariantForHull(String hullId) {
        for (String vid : Global.getSettings().getAllVariantIds()) {
            if (vid.startsWith(hullId + "_")) {
                return vid;
            }
        }
        // Fallback: assume vanilla `_Standard` exists. addToFleet will throw
        // if it doesn't, surfacing a clear error rather than silent retreat.
        return hullId + "_Standard";
    }

    /** Null-safe accessor for FleetMember variant id. */
    private static String safeVariantId(FleetMemberAPI member) {
        try {
            ShipVariantAPI v = member.getVariant();
            return v == null ? "<null>" : safeVariantHullId(v);
        } catch (Throwable t) {
            return "<error:" + t.getClass().getSimpleName() + ">";
        }
    }

    /** Null-safe accessor for variant hull-variant id. */
    private static String safeVariantHullId(ShipVariantAPI v) {
        try {
            return v.getHullVariantId();
        } catch (Throwable t) {
            return "<error:" + t.getClass().getSimpleName() + ">";
        }
    }

    /** Null-safe accessor for FleetMember id. */
    private static String safeMemberId(FleetMemberAPI member) {
        try {
            return member.getId();
        } catch (Throwable t) {
            return "<error:" + t.getClass().getSimpleName() + ">";
        }
    }

    private static String staticVariantWeaponMap(
            ShipVariantAPI variant, MatchupConfig.BuildSpec spec) {
        if (variant == null || spec == null) return "<null>";
        java.util.TreeMap<String, String> out = new java.util.TreeMap<String, String>();
        for (String slotId : spec.weaponAssignments.keySet()) {
            try {
                out.put(slotId, String.valueOf(variant.getWeaponId(slotId)));
            } catch (Throwable t) {
                out.put(slotId, "<error:" + t.getClass().getSimpleName() + ">");
            }
        }
        return out.toString();
    }

    private static String staticVariantHullmods(ShipVariantAPI variant) {
        try {
            java.util.List<String> mods = new java.util.ArrayList<String>(
                    variant.getNonBuiltInHullmods());
            java.util.Collections.sort(mods);
            return mods.toString();
        } catch (Throwable t) {
            return "<error:" + t.getClass().getSimpleName() + ">";
        }
    }

    private static int safeFluxVents(ShipVariantAPI variant) {
        try { return variant.getNumFluxVents(); }
        catch (Throwable t) { return -1; }
    }

    private static int safeFluxCaps(ShipVariantAPI variant) {
        try { return variant.getNumFluxCapacitors(); }
        catch (Throwable t) { return -1; }
    }

    /**
     * Probe-mode helper: add a ship with an EMPTY variant (no hullmods, no
     * weapons) to the given side. Uses createEmptyVariant + createFleetMember
     * + addFleetMember so the probe sees a pristine hull without stock-variant
     * contamination (e.g. onslaught_Standard's built-in dedicated_targeting_core
     * which would falsely mark ITU as inapplicable to all capitals).
     *
     * Retreat-mode pitfall noted in .claude/skills/starsector-modding.md is
     * irrelevant here — the plugin exits the JVM via System.exit(0) before
     * any ship can execute its first AI tick.
     */
    private static void addProbeShipEmptyVariant(
            MissionDefinitionAPI api, FleetSide side, String hullId) {
        try {
            ShipHullSpecAPI hullSpec = Global.getSettings().getHullSpec(hullId);
            if (hullSpec == null) {
                log.warn("addProbeShipEmptyVariant: hullSpec not found for "
                        + hullId + "; skipping");
                return;
            }
            ShipVariantAPI variant = Global.getSettings().createEmptyVariant(
                    hullId + "_manifest_probe", hullSpec);
            if (variant == null) {
                log.warn("addProbeShipEmptyVariant: createEmptyVariant returned null for "
                        + hullId + "; skipping");
                return;
            }
            FleetMemberAPI member = Global.getSettings().createFleetMember(
                    FleetMemberType.SHIP, variant);
            if (member == null) {
                log.warn("addProbeShipEmptyVariant: createFleetMember returned null for "
                        + hullId + "; skipping");
                return;
            }
            // CR=0 causes ships to deploy disabled and insta-die. 0.7 is the
            // vanilla standard deployment CR and is what the skill
            // (.claude/skills/starsector-modding.md) mandates.
            member.getRepairTracker().setCR(0.7f);
            api.addFleetMember(side, member);
            log.info("addProbeShipEmptyVariant: hull=" + hullId
                    + " side=" + side + " added empty variant");
        } catch (Throwable t) {
            log.error("addProbeShipEmptyVariant: failed for " + hullId, t);
        }
    }
}
