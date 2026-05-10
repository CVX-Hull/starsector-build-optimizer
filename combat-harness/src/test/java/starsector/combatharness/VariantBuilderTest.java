package starsector.combatharness;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.HashSet;
import java.util.Set;

/**
 * Tests for VariantBuilder.uniqueVariantId — the cross-matchup variant
 * cache fix (2026-05-10). The full createVariant path requires a
 * Starsector engine context; we cover the suffix-generation logic in
 * isolation. Engine-side behavior is verified empirically by Wave 2/3
 * loadout-mismatch rates dropping from C2's 3.67 % / C3's 19 % to ~0 %.
 */
class VariantBuilderTest {

    @Test
    void uniqueVariantIdPreservesBase() {
        String base = "hammerhead_opt_000019";
        String unique = VariantBuilder.uniqueVariantId(base);
        assertTrue(
            unique.startsWith(base + "__"),
            "unique id must start with base id, got: " + unique
        );
    }

    @Test
    void uniqueVariantIdSuffixIsHexOfConfiguredLength() {
        String unique = VariantBuilder.uniqueVariantId("base");
        String suffix = unique.substring("base__".length());
        int n = VariantBuilder.UNIQUE_VARIANT_SUFFIX_HEX_CHARS;
        assertEquals(n, suffix.length(), "suffix length");
        assertTrue(suffix.matches("[0-9a-f]{" + n + "}"),
                "suffix is hex: " + suffix);
    }

    @Test
    void uniqueVariantIdIsDistinctAcrossCalls() {
        // 1000 calls — collision probability for 8-char hex suffix is
        // 1000^2 / (2 * 16^8) ~= 1.16e-4. Single-run flakes are highly
        // unlikely; if this test ever flakes, the suffix length is too
        // short for the call rate.
        Set<String> seen = new HashSet<>();
        String base = "wolf_opt_000007";
        for (int i = 0; i < 1000; i++) {
            String unique = VariantBuilder.uniqueVariantId(base);
            assertFalse(
                seen.contains(unique),
                "duplicate unique variantId at iteration " + i + ": " + unique
            );
            seen.add(unique);
        }
    }

    @Test
    void emptyBaseStillProducesValidId() {
        String unique = VariantBuilder.uniqueVariantId("");
        assertTrue(unique.startsWith("__"));
        assertEquals(
            "__".length() + VariantBuilder.UNIQUE_VARIANT_SUFFIX_HEX_CHARS,
            unique.length()
        );
    }
}
