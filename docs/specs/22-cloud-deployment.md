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

## GPU Requirement

**Starsector requires hardware OpenGL for acceptable simulation speed.** Tested 2026-04-12: a Hetzner CCX33 (CPU-only, software Mesa/llvmpipe rendering) produced only 26s of game-time in 120s wall-clock — ~10-50x slower than local with a GPU. Xvfb provides the X11 display server, but LWJGL rendering needs a real GPU driver for the 5x time-multiplied simulation speeds.

**Implication:** CPU-only cloud providers (Hetzner Cloud CCX, most basic VMs) are not viable. Cloud deployment requires either:
- GPU instances (AWS g4dn with T4, ~$0.16-0.25/hr spot)
- Or local execution with a real GPU

For most optimization runs, **local execution is recommended** (simpler, free, fast with host GPU).

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

## Cloud Machine Sizing (GPU Required)

| Machine | vCPUs | RAM | GPU | Instances | Spot Cost/hr | Use |
|---------|-------|-----|-----|-----------|-------------|-----|
| AWS g4dn.xlarge | 4 | 16GB | T4 | 4 | ~$0.16 | Minimum viable |
| AWS g4dn.2xlarge | 8 | 32GB | T4 | 8 | ~$0.25 | **Recommended** |
| AWS g4dn.4xlarge | 16 | 64GB | T4 | 12 | ~$0.36 | High throughput |

Hetzner CCX (no GPU) is **not viable** — software rendering is too slow.

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

**Key packages discovered during testing (2026-04-12):**
- `libxcursor1`, `libxxf86vm1` — required by LWJGL native libraries (`liblwjgl64.so`)
- `libopenal1` — OpenAL audio (without it, game shows blocking error dialog)
- `libasound2t64` — ALSA base (with null config above, prevents sound card errors)
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
