# Technical Debt

Pre-existing issues discovered during audit phases. Each entry includes the violation, location, and suggested fix.

---

## Java — Magic Numbers in CombatHarnessPlugin

**File:** `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`

**Invariant violated:** No magic numbers in function bodies (CLAUDE.md § Design Invariants).

### Spawn coordinates

```java
.spawnFleetMember(member, new Vector2f(-2000f, yOffset), 0f, 0f);  // player
.spawnShipOrWing(variantId, new Vector2f(2000f, yOffset), 180f);   // enemy
```

Four bare literals: player X (`-2000f`), enemy X (`2000f`), player facing (`0f`), enemy facing (`180f`). These are arena geometry constants.

**Fix:** Extract to named constants:
```java
private static final float PLAYER_SPAWN_X = -2000f;
private static final float ENEMY_SPAWN_X = 2000f;
private static final float PLAYER_FACING = 0f;
private static final float ENEMY_FACING = 180f;
```

### Cleanup frame count

```java
cleanupFramesLeft = 3;
if (cleanupFramesLeft == 3) {
```

The number of frames to wait for entity cleanup is a bare `3`.

**Fix:**
```java
private static final int CLEANUP_FRAMES = 3;
```

**Found during:** Phase T2 post-implementation audit (2026-04-11).

---

## Python — Undeclared `_game_log_file` on GameInstance

**File:** `src/starsector_optimizer/instance_manager.py`, `_start_game()` method.

```python
inst._game_log_file = open(log_path, "w")
```

`GameInstance` is a `@dataclass` but `_game_log_file` is added dynamically — not declared as a field. The file handle is never closed, leaking file descriptors across instance restarts.

**Fix:** Declare the field and close it in `_kill_instance()`:
```python
# In GameInstance:
_game_log_file: typing.IO | None = field(default=None, repr=False)

# In _kill_instance():
if inst._game_log_file and not inst._game_log_file.closed:
    inst._game_log_file.close()
```

**Found during:** Phase T2 post-implementation audit (2026-04-11).

---

## Python Tests — Bare MagicMock Without spec=

**File:** `tests/test_instance_manager.py` (17 instances).

```python
mock_proc = MagicMock()           # no spec
mock_proc.poll.return_value = 1
```

Process mocks lack `spec=subprocess.Popen`. Tests pass because the correct methods (`poll`, `terminate`, `wait`, `kill`) are configured, but the mocks don't enforce interface correctness — a typo like `mock_proc.pooll` would silently pass.

**Fix:** Add `spec=subprocess.Popen` to all process mock creation sites:
```python
mock_proc = MagicMock(spec=subprocess.Popen)
```

**Found during:** Phase T2 post-implementation audit (2026-04-11).
