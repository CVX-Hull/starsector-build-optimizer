# Phase 6 cost model output (2026-04-18)

## Provider: AWS c7a.2xlarge spot us-east-1
- VM price: $0.15/hr
- Throughput: 122.0 matchups/hr/VM
- $/matchup: $0.0012
- Preemption rate: 3.0%

## Provider: Hetzner CCX33
- VM price: $0.13/hr
- Throughput: 119.8 matchups/hr/VM
- $/matchup: $0.0011
- Preemption rate: 0.0%

## AWS quota (2026-04-18 audit)
| Region | Spot vCPU | 8-vCPU VMs |
|---|---|---|
| us-east-1 | 640 | 80 |
| us-east-2 | 640 | 80 |
| us-west-1 | 256 | 32 |
| us-west-2 | 256 | 32 |
| **Total** | **1792** | **224** |

Us-east-1 + us-east-2 alone: **160 VMs** (covers the 96-VM prep target with slack)

## Sampler benchmark — 2 hulls × 3 samplers × 1.0 hr each

| Sampler | Workers | VMs | hrs | Trials/run | Cost/hull | Cost (2 hulls) |
|---|---|---|---|---|---|---|
| TPE-24 | 24 | 12 | 1.0 | 146 | $1.85 | $3.71 |
| CatCMAwM-24 | 24 | 12 | 1.0 | 146 | $1.85 | $3.71 |
| CatCMAwM-48 | 48 | 24 | 1.0 | 293 | $3.71 | $7.42 |

Total benchmark cost: **$14.83** (96 VM-hrs, ~1.0 hr wall-clock at peak 96 VMs)

## Phase 7 prep campaign — 8 hulls × early × 600 trials

- Workers per study: 24 (12 VMs × 2 JVMs)
- Wall-clock per study: 4.10 hr (studies run in parallel)
- Total VMs at peak: 96
- Total VM-hours: 393.4
- Cost (incl 3% preemption reruns): **$60.79**

## Budget rollup

| Line item | Cost |
|---|---|
| Validation probe (2 VMs × 2 regions × 15 min) | $0.15 |
| Pipeline smoke (1 study × 8 workers × 2 hr) | $1.20 |
| Sampler benchmark (2h × 3 samplers × 2 hulls) | $14.83 |
| Prep campaign (8 hulls × 600 trials) | $60.79 |
| **Subtotal** | **$76.97** |
| Slack (rerun, retries, headroom) | $5.00 |
| **Recommended budget (rounded up to $5)** | **$85.00** |

## Sensitivity: prep cost vs trials_per_study

| Trials/study | Hrs/study | Cost (AWS, incl preemption) |
|---|---|---|
| 400 | 2.73 | $40.52 |
| 500 | 3.42 | $50.66 |
| 600 | 4.10 | $60.79 |
| 700 | 4.78 | $70.92 |
| 800 | 5.46 | $81.05 |

## Sensitivity: prep cost vs n_hulls (at 600 trials)

| Hulls | Cost (AWS) |
|---|---|
| 4 | $30.39 |
| 6 | $45.59 |
| 8 | $60.79 |
| 10 | $75.98 |
| 12 | $91.18 |

