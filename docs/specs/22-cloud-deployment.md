# Cloud Deployment Specification

Automates provisioning, deployment, and teardown of cloud machines for batch combat simulation. Defined in `scripts/cloud/`.

## Overview

Local-first development with cloud burst for expensive simulation. Optuna studies persist via SQLite file transfer.

```
Local machine (dev + heuristic + small sim)
  ↑ scp study.db + game + optimizer to cloud
  → Cloud machines run parallel sim
  ↑ scp study.db + results back
  → Local analysis + visualization
```

## GPU Requirement — REVISED 2026-04-18

**CPU-only cloud instances are fully viable for Starsector simulation.** The original "GPU required" conclusion from 2026-04-12 was a misdiagnosis: Starsector was crashing on startup with a LWJGL 2.x bug, not running-but-slow under software rendering.

**The real bug:** LWJGL 2.x's `LinuxDisplay.getAvailableDisplayModes` throws `ArrayIndexOutOfBoundsException: Index 0 out of bounds for length 0` when Xvfb's XRandR extension has not populated its mode list. Xvfb does not finalize XRandR state until a client queries it — so the first call from LWJGL returns an empty array and crashes.

**The fix** (now in `instance_manager.py::_start_xvfb`): after waiting for the Xvfb socket, run `xrandr --query` once as a client to warm the XRandR extension. This makes LWJGL's enumeration succeed. Requires `x11-xserver-utils` in cloud-init.

**Benchmarks (2026-04-18, `experiments/cloud-benchmark-2026-04-18/`):**

| Provider | Instance | Spot $/hr | Matchups/hr/inst | vs local (27/hr/inst) |
|---|---|---|---|---|
| Local workstation | 12-core, RTX 4090 | $0 | 27 | 1× baseline |
| AWS c7i.2xlarge | 8 vCPU Intel SPR, us-east-1 | $0.158 | **64** | 2.4× |
| Hetzner CCX33 | 8 vCPU AMD Milan, Ashburn VA | $0.13 | **~63** | 2.3× |

Both CPU cloud paths match or exceed local per-instance throughput at negligible cost. **GPU instances are not required.**

## Local Machine Sizing (Recommended)

Measured 2026-04-12 on dev machine (12-core, 64GB RAM, RTX 4090):

| Metric | Value | Notes |
|--------|-------|-------|
| Threads per instance | 67 (1 dominant) | Effectively single-threaded main loop |
| CPU per instance | ~1 core | Main thread pegs one core at 100% |
| RAM per instance | ~3.4 GB | JVM heap 2GB + native/LWJGL + Xvfb |
| GPU per instance | Minimal | Shared OpenGL context, trivial for modern GPUs |
| **Recommended instances** | **8** | 12 cores - 2 for OS/Python - 2 headroom |

64GB RAM → 8 instances × 3.4GB = 27GB, well within limits. RTX 4090 handles 8+ instances trivially.

## Cloud Machine Sizing (CPU-only, updated 2026-04-18)

Per-JVM cost: ~2.5 vCPU under combat (same rule as local, `num_instances ≤ cpu_count()//3`).

| Machine | vCPUs | RAM | Max instances | Spot/hr | $/matchup | Notes |
|---------|-------|-----|---------------|---------|-----------|-------|
| Hetzner CCX33 (Ashburn) | 8 ded AMD Milan | 32 GB | 2 | **$0.13** | ~$0.001 | Cheapest; no spot tier (no preemption). Existing scripts target. |
| AWS c7i.2xlarge (us-east-1) | 8 Intel SPR | 16 GB | 2 | $0.158 | $0.00123 | Validated 2026-04-18. |
| AWS c7a.2xlarge (us-west-2) | 8 AMD Genoa | 16 GB | 2 | ~$0.15 | ~$0.001 | Recommended for AWS if scaling up — 2-4× lower interruption vs c7i. |
| AWS c7i.4xlarge / c7a.4xlarge | 16 | 32 GB | 5 | ~$0.27 | — | Higher density if single-instance blast-radius acceptable. |
| GCP n2d-standard-8 spot | 8 AMD Milan | 32 GB | 2 | $0.07 | ~$0.0005 | Half the cost of AWS; requires quota bump to 240 vCPU. |

Previous GPU recommendation (g4dn.xlarge, T4) is obsolete — GPU is not required for Starsector headless simulation once XRandR warmup is in place.

## Cloud-Init Script (GPU Instance)

```yaml
#cloud-config
package_update: true
packages:
  - xvfb
  - xdotool
  - rsync
  - curl
  - libgl1
  - libasound2t64
  - libxi6
  - libxrender1
  - libxtst6
  - libxrandr2
  - libxcursor1
  - libxxf86vm1
  - libopenal1

runcmd:
  - curl -LsSf https://astral.sh/uv/install.sh | sh
  # Null ALSA config for headless audio (prevents OpenAL error dialog)
  - |
    cat > /etc/asound.conf << 'EOF'
    pcm.!default { type null }
    ctl.!default { type null }
    EOF
  - touch /tmp/cloud-init-done
```

**Key packages discovered during testing (2026-04-12, updated 2026-04-18):**
- `libxcursor1`, `libxxf86vm1` — required by LWJGL native libraries (`liblwjgl64.so`)
- `libopenal1` — OpenAL audio (without it, game shows blocking error dialog)
- `libasound2t64` — ALSA base (with null config above, prevents sound card errors)
- `x11-xserver-utils` — provides `xrandr` binary needed by `instance_manager.py::_start_xvfb` for XRandR warmup (without it, LWJGL 2.x crashes on first Starsector launch)
- **No `openjdk`** — game bundles its own JRE (`jre_linux/`), system Java is unnecessary and can interfere

## Deployment Script

See `scripts/cloud/deploy.sh`. Key details:

- **Game directory**: Must use project's `game/starsector/` (contains combat-harness mod), not a separate installation
- **rsync `--delete`**: Required to prevent stale files from a different game version causing JRE crashes (e.g., leftover `lib/ext/` directory)
- **Game activation**: Copy `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (contains serial key) to cloud machine
- **SSH key**: `~/.ssh/starsector-opt` (ed25519, uploaded to cloud provider as `starsector-opt`)

```bash
# Usage: ./deploy.sh [num_machines] [server_type] [location]
scripts/cloud/deploy.sh 1 g4dn.2xlarge us-east-1
```

## Work Distribution

Each machine gets a list of hull IDs to optimize. The local orchestrator partitions hulls across machines.

```bash
# Start optimization (backgrounded on remote)
scripts/cloud/run.sh hammerhead 0 --sim-budget 200 --active-opponents 10
```

## Result Collection

```bash
scripts/cloud/collect.sh    # pulls study.db + eval logs
scripts/cloud/status.sh     # check if optimizer is running
scripts/cloud/teardown.sh   # delete machines
```

## Optuna Study Persistence

**TPESampler is stateless** — it reconstructs its model from stored trials on every call. Transferring the SQLite file preserves all knowledge.

**Local → Cloud workflow:**
1. Local: create study in `study.db`, add heuristic warm-start trials, run small sim validation
2. `scp study.db` to cloud machine
3. Cloud: `optuna.load_study(storage="sqlite:///study.db")`, continue with `n_jobs=8`
4. `scp study.db` back to local for analysis

**Multi-machine merge:** Each machine runs an independent study on different hulls. No study merging needed — each hull gets its own study. Results are in the shared JSONL evaluation log.

**Zombie trial cleanup:** If a machine crashes, trials may be stuck in RUNNING state. Clean up before resuming:
```python
for trial in study.trials:
    if trial.state == optuna.trial.TrialState.RUNNING:
        study.tell(trial.number, state=optuna.trial.TrialState.FAIL)
```

## Setup Time Breakdown

| Step | Time |
|------|------|
| Machine creation | ~10-30s |
| Server boot + cloud-init | ~60-90s |
| rsync game dir (361MB over 1Gbps) | ~5-10s |
| rsync optimizer code | ~3s |
| Copy prefs.xml | ~1s |
| `uv sync` | ~15s |
| **Total** | **~2 minutes** |

## Cost Estimates

### Local (Recommended)
| Scenario | Instances | Sims | Wall-clock | Cost |
|----------|-----------|------|------------|------|
| 1 hull (hammerhead) | 8 | ~1000 | ~1.5h | $0 |
| 3 dev hulls | 8 | ~3000 | ~4.5h | $0 |
| 10 priority hulls | 8 | ~10K | ~15h | $0 |

### Cloud (GPU instances, when local isn't enough)
| Scenario | Machines | Instances | Sims | Wall-clock | Cost |
|----------|----------|-----------|------|------------|------|
| 10 priority hulls | 1 × g4dn.2xl | 8 | ~10K | ~15h | ~$3.75 |
| 50 hulls | 3 × g4dn.2xl | 24 | ~50K | ~20h | ~$15 |
| 50 hulls + QD | 3 × g4dn.2xl | 24 | ~65K | ~26h | ~$20 |

All well within $30 budget. Dominant cost is development time, not compute.

## Lessons Learned (2026-04-12 Hetzner Test)

1. **Software rendering is a dealbreaker.** Mesa/llvmpipe on CPU-only VMs makes Starsector unplayably slow. The game loop ties simulation speed to frame rendering — slow frames = slow simulation.
2. **Missing native libraries cause silent failures.** LWJGL needs `libxcursor1` and `libxxf86vm1` beyond the obvious X11 libs. Without them, the game crashes with `UnsatisfiedLinkError` in `liblwjgl64.so`.
3. **OpenAL error blocks the launcher.** Missing audio produces a modal dialog that prevents the "Play Starsector" click from working. Fix: install `libopenal1` + null ALSA config.
4. **rsync without `--delete` leaves stale files.** If a different game version was previously synced, leftover files (e.g., `jre_linux/lib/ext/`) cause JRE startup failures.
5. **Game bundles its own JRE.** Installing system Java is unnecessary and the `JAVA_HOME` can interfere with the bundled JRE.
6. **Game activation via prefs.xml works.** Copying `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (contains `serial` key) transfers activation to new machines.
