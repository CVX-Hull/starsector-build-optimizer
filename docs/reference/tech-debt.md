# Technical Debt

Pre-existing issues found by post-implementation audit agents. Address when touching the relevant code.

---

*No outstanding items. All previous items have been resolved:*

- *#1 (Xvfb poll timeout): Added dedicated `xvfb_ready_timeout_seconds` config field*
- *#2/#16 (game_dir property): Added to spec 18 InstancePool*
- *#3 (_game_log_file): Added to spec 18 GameInstance*
- *#4 (hash_build hull_id): Updated spec 24*
- *#5 (_InFlightBuild ordering): Updated spec 24*
- *#6 (stale evaluate() reference): Already fixed in spec 09*
- *#7 (engagement_threshold duplication): Embedded CombatFitnessConfig in OptimizerConfig*
- *#8 (bare MagicMock): Added spec=CombatResult*
- *#9 (hardcoded eval_log_path): Added to OptimizerConfig*
- *#10 (useDefaultAI confusion): Documented in code and starsector-modding skill*
- *#11 (alphabetical opponent selection): Resolved — anchor-first + incumbent-overlap selection shipped (Phase 5C). See docs/reference/phase5c-opponent-curriculum.md*
- *#12 (heartbeat timeout): Fixed (Robot pixel-polling + heartbeat touch-not-delete)*
- *#13 (spec 16 coordinates): Updated to match code*
- *#14 (TitleScreenPlugin triggered reset): Documented in spec 16*
- *#15 (_assign_next_batch heartbeat): Documented touch-not-delete in spec 18*
