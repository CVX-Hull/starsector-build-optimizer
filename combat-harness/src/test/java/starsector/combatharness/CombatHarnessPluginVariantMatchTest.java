package starsector.combatharness;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.HashSet;
import java.util.Set;

/**
 * Tests for {@link CombatHarnessPlugin#matchesAnyExpectedVariantId}, the
 * doSetup matching helper that bridges the legacy unsuffixed variantId
 * form and the V2 cache-disambiguation form
 * {@code base + "__" + 8 hex chars} produced by
 * {@link VariantBuilder#uniqueVariantId}.
 *
 * <p>Without this two-form match, {@code expectedVariantIds.contains(vid)}
 * fails on every matchup once {@code uniqueVariantId} starts appending
 * suffixes — regression mode is 100% SETUP_TIMEOUT, fitness=-2.0,
 * stale_owner0=1.
 */
class CombatHarnessPluginVariantMatchTest {

    @Test
    void exactBaseMatches() {
        Set<String> expected = setOf("honest__wave1-c0a__s0__seed0__rank1__variant");
        assertTrue(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                "honest__wave1-c0a__s0__seed0__rank1__variant", expected));
    }

    @Test
    void suffixedFormMatchesWhenBaseExpected() {
        String base = "honest__wave1-c0a__s0__seed0__rank1__variant";
        String suffixed = VariantBuilder.uniqueVariantId(base);
        Set<String> expected = setOf(base);
        assertTrue(CombatHarnessPlugin.matchesAnyExpectedVariantId(suffixed, expected),
                "suffixed id should match its base via the V2 path: " + suffixed);
    }

    @Test
    void unrelatedSuffixedIdDoesNotMatch() {
        Set<String> expected = setOf("hammerhead_opt_000007");
        // Suffix-shaped, but base != expected.
        assertFalse(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                "wolf_opt_000003__deadbeef", expected));
    }

    @Test
    void wrongLengthSuffixDoesNotMatch() {
        Set<String> expected = setOf("hammerhead_opt_000007");
        // 7 hex chars (off by one) — must not match.
        assertFalse(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                "hammerhead_opt_000007__deadbee", expected));
        // 9 hex chars — must not match either.
        assertFalse(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                "hammerhead_opt_000007__deadbeef0", expected));
    }

    @Test
    void nonHexSuffixDoesNotMatch() {
        Set<String> expected = setOf("hammerhead_opt_000007");
        // Right length and __ separator, but Z is not hex.
        assertFalse(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                "hammerhead_opt_000007__deadbeeZ", expected));
    }

    @Test
    void singleUnderscoreSeparatorDoesNotMatch() {
        Set<String> expected = setOf("hammerhead_opt_000007");
        // VariantBuilder uses double underscore — single must not match.
        assertFalse(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                "hammerhead_opt_000007_deadbeef", expected));
    }

    @Test
    void nullVidDoesNotMatch() {
        assertFalse(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                null, setOf("hammerhead_opt_000007")));
    }

    @Test
    void emptyExpectedSetDoesNotMatch() {
        assertFalse(CombatHarnessPlugin.matchesAnyExpectedVariantId(
                "hammerhead_opt_000007", new HashSet<String>()));
    }

    @Test
    void multipleExpectedAnyMatches() {
        Set<String> expected = setOf("a", "honest__wave1__rank1__variant", "z");
        String suffixed = VariantBuilder.uniqueVariantId("honest__wave1__rank1__variant");
        assertTrue(CombatHarnessPlugin.matchesAnyExpectedVariantId(suffixed, expected));
    }

    private static Set<String> setOf(String... ids) {
        Set<String> s = new HashSet<>();
        for (String id : ids) s.add(id);
        return s;
    }
}
