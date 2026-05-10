package starsector.combatharness;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.UUID;

/** Per-mission trace identifiers shared across the combat harness. */
public final class HarnessTraceContext {

    private static String missionUuid = "uninitialized";
    private static String missionQueueHash = "uninitialized";
    private static String missionMatchupId = "uninitialized";

    private HarnessTraceContext() {}

    public static synchronized void startMission(String queueHash, String matchupId) {
        missionUuid = UUID.randomUUID().toString();
        missionQueueHash = queueHash != null ? queueHash : "<null>";
        missionMatchupId = matchupId != null ? matchupId : "<null>";
    }

    public static synchronized String missionUuid() {
        return missionUuid;
    }

    public static synchronized String missionQueueHash() {
        return missionQueueHash;
    }

    public static synchronized JSONObject toJSON(String pluginQueueHash)
            throws JSONException {
        JSONObject out = new JSONObject();
        out.put("mission_uuid", missionUuid);
        out.put("mission_queue_hash", missionQueueHash);
        out.put("plugin_queue_hash", pluginQueueHash != null ? pluginQueueHash : "<null>");
        out.put("mission_matchup_id", missionMatchupId);
        return out;
    }

    public static synchronized String summary(String pluginQueueHash) {
        return "mission_uuid=" + missionUuid
                + " mission_queue_hash=" + missionQueueHash
                + " plugin_queue_hash=" + (pluginQueueHash != null ? pluginQueueHash : "<null>")
                + " mission_matchup_id=" + missionMatchupId;
    }
}
