package starsector.combatharness;

import com.fs.starfarer.api.Global;

import org.json.JSONArray;
import org.json.JSONException;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Container for a batch of matchup configs. Reads a JSON array from saves/common/.
 */
public class MatchupQueue {

    public static final String QUEUE_FILE = MatchupConfig.COMMON_PREFIX + "queue.json";

    private final List<MatchupConfig> matchups;

    private MatchupQueue(List<MatchupConfig> matchups) {
        this.matchups = Collections.unmodifiableList(matchups);
    }

    /** Load queue from saves/common/combat_harness_queue.json via SettingsAPI. */
    public static MatchupQueue loadFromCommon() throws JSONException {
        try {
            String content = Global.getSettings().readTextFileFromCommon(QUEUE_FILE);
            return fromJSON(new JSONArray(content));
        } catch (JSONException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException("Failed to read matchup queue from saves/common/" + QUEUE_FILE, e);
        }
    }

    /** Check if queue file exists in saves/common/. */
    public static boolean existsInCommon() {
        return Global.getSettings().fileExistsInCommon(QUEUE_FILE);
    }

    /** Parse a JSON array of matchup config objects. */
    public static MatchupQueue fromJSON(JSONArray array) throws JSONException {
        if (array.length() == 0) {
            throw new IllegalArgumentException("Matchup queue must be non-empty");
        }
        List<MatchupConfig> matchups = new ArrayList<MatchupConfig>();
        for (int i = 0; i < array.length(); i++) {
            matchups.add(MatchupConfig.fromJSON(array.getJSONObject(i)));
        }
        return new MatchupQueue(matchups);
    }

    /** Serialize back to JSONArray for round-trip testing. */
    public JSONArray toJSON() throws JSONException {
        JSONArray array = new JSONArray();
        for (MatchupConfig config : matchups) {
            array.put(config.toJSON());
        }
        return array;
    }

    public int size() {
        return matchups.size();
    }

    public MatchupConfig get(int index) {
        return matchups.get(index);
    }

    public boolean isEmpty() {
        return matchups.isEmpty();
    }
}
