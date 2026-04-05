package starsector.combatharness;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class DamageTrackerTest {

    private DamageTracker tracker;

    @BeforeEach
    void setUp() {
        tracker = new DamageTracker();
    }

    @Test
    void getOrCreateReturnsNewAccumulator() {
        DamageTracker.ShipDamageAccumulator acc = tracker.getOrCreate("ship_0");
        assertNotNull(acc);
        assertEquals(0f, acc.shieldDamageDealt);
        assertEquals(0f, acc.hullDamageTaken);
    }

    @Test
    void getOrCreateReturnsSameInstance() {
        DamageTracker.ShipDamageAccumulator acc1 = tracker.getOrCreate("ship_0");
        DamageTracker.ShipDamageAccumulator acc2 = tracker.getOrCreate("ship_0");
        assertSame(acc1, acc2);
    }

    @Test
    void recordDamageAccumulatesOnSource() {
        tracker.recordDamage("attacker", "target", 100f, 200f, 50f, 10f);

        DamageTracker.ShipDamageAccumulator attAcc = tracker.getOrCreate("attacker");
        assertEquals(100f, attAcc.shieldDamageDealt, 0.01f);
        assertEquals(200f, attAcc.armorDamageDealt, 0.01f);
        assertEquals(50f, attAcc.hullDamageDealt, 0.01f);
        assertEquals(10f, attAcc.empDamageDealt, 0.01f);
    }

    @Test
    void recordDamageAccumulatesOnTarget() {
        tracker.recordDamage("attacker", "target", 100f, 200f, 50f, 10f);

        DamageTracker.ShipDamageAccumulator tgtAcc = tracker.getOrCreate("target");
        assertEquals(100f, tgtAcc.shieldDamageTaken, 0.01f);
        assertEquals(200f, tgtAcc.armorDamageTaken, 0.01f);
        assertEquals(50f, tgtAcc.hullDamageTaken, 0.01f);
        assertEquals(10f, tgtAcc.empDamageTaken, 0.01f);
    }

    @Test
    void multipleHitsAccumulate() {
        tracker.recordDamage("attacker", "target", 100f, 0f, 0f, 0f);
        tracker.recordDamage("attacker", "target", 150f, 0f, 0f, 0f);

        DamageTracker.ShipDamageAccumulator attAcc = tracker.getOrCreate("attacker");
        assertEquals(250f, attAcc.shieldDamageDealt, 0.01f);

        DamageTracker.ShipDamageAccumulator tgtAcc = tracker.getOrCreate("target");
        assertEquals(250f, tgtAcc.shieldDamageTaken, 0.01f);
    }

    @Test
    void multipleAttackersTrackedSeparately() {
        tracker.recordDamage("ship_a", "target", 100f, 0f, 0f, 0f);
        tracker.recordDamage("ship_b", "target", 200f, 0f, 0f, 0f);

        assertEquals(100f, tracker.getOrCreate("ship_a").shieldDamageDealt, 0.01f);
        assertEquals(200f, tracker.getOrCreate("ship_b").shieldDamageDealt, 0.01f);
        assertEquals(300f, tracker.getOrCreate("target").shieldDamageTaken, 0.01f);
    }

    @Test
    void getAccumulatorsReturnsAllTracked() {
        tracker.recordDamage("a", "b", 1f, 0f, 0f, 0f);
        assertEquals(2, tracker.getAccumulators().size());
        assertTrue(tracker.getAccumulators().containsKey("a"));
        assertTrue(tracker.getAccumulators().containsKey("b"));
    }

    @Test
    void resetClearsAllAccumulators() {
        tracker.recordDamage("a", "b", 100f, 0f, 0f, 0f);
        assertEquals(2, tracker.getAccumulators().size());

        tracker.reset();

        assertTrue(tracker.getAccumulators().isEmpty());
    }

    @Test
    void getOrCreateReturnsFreshAfterReset() {
        tracker.recordDamage("a", "b", 100f, 0f, 0f, 0f);
        tracker.reset();

        DamageTracker.ShipDamageAccumulator acc = tracker.getOrCreate("a");
        assertEquals(0f, acc.shieldDamageDealt, 0.01f);
        assertEquals(0f, acc.shieldDamageTaken, 0.01f);
    }

    @Test
    void newDamageAfterResetStartsFresh() {
        tracker.recordDamage("a", "b", 100f, 0f, 0f, 0f);
        tracker.reset();
        tracker.recordDamage("a", "b", 50f, 0f, 0f, 0f);

        assertEquals(50f, tracker.getOrCreate("a").shieldDamageDealt, 0.01f);
        assertEquals(50f, tracker.getOrCreate("b").shieldDamageTaken, 0.01f);
    }
}
