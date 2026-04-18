"""Phase 6 cost + throughput model.

All numbers in this script are derived from:
- experiments/cloud-benchmark-2026-04-18/ (57-matchup 6-build AWS + Hetzner bench, 2026-04-18)
- docs/reference/phase6-cloud-worker-federation.md (§3 pricing, §1 TPE saturation ceiling)
- AWS quota audit 2026-04-18 (aws service-quotas get-service-quota L-34B43A08, 4 US regions)
- AWS spot pricing us-east-1 c7a.2xlarge 2026-04-18 (~$0.15/hr point estimate)

Run: uv run python experiments/phase6-planning/cost_model.py
"""

from dataclasses import dataclass


# ---------- Constants (pinned from benchmark + pricing) ----------

@dataclass(frozen=True)
class Provider:
    name: str
    vm_hourly_usd: float         # spot pricing point estimate
    matchups_per_hr_per_vm: float  # 2 JVMs per c7a.2xlarge / CCX33 at ~61 m/hr/JVM
    preemption_rate: float       # fraction of VM-hr lost to preemption


AWS_C7A_SPOT = Provider(
    name="AWS c7a.2xlarge spot us-east-1",
    vm_hourly_usd=0.15,
    matchups_per_hr_per_vm=122.0,  # c7i bench: 64.2 m/hr/inst; 2 JVMs × 61 = 122
    preemption_rate=0.03,          # price-capacity-optimized + CapacityRebalancing
)

HETZNER_CCX33 = Provider(
    name="Hetzner CCX33",
    vm_hourly_usd=0.13,
    matchups_per_hr_per_vm=119.8,  # 2 × 59.9 m/hr/JVM per bench
    preemption_rate=0.0,
)

# Derived
def cost_per_matchup(p: Provider) -> float:
    return p.vm_hourly_usd / p.matchups_per_hr_per_vm


# ---------- Optimization constants ----------

MATCHUPS_PER_TRIAL = 10          # active_opponents default
TPE_SATURATION_WORKERS = 24      # above this, TPE constant_liar collapses to random
JVMS_PER_VM = 2                  # 8 vCPU / 3-cores-per-JVM sizing rule

# AWS quota from 2026-04-18 audit
AWS_SPOT_VCPU_BY_REGION = {
    "us-east-1": 640,
    "us-east-2": 640,
    "us-west-1": 256,
    "us-west-2": 256,
}
VCPU_PER_VM = 8


def aws_spot_vm_capacity(regions: list[str]) -> int:
    return sum(AWS_SPOT_VCPU_BY_REGION[r] for r in regions) // VCPU_PER_VM


# ---------- Cost primitives ----------

def run_cost(vms: int, hours: float, p: Provider) -> float:
    """Raw VM-hour cost, no preemption overhead."""
    return vms * hours * p.vm_hourly_usd


def run_matchups(vms: int, hours: float, p: Provider) -> float:
    return vms * hours * p.matchups_per_hr_per_vm


def trials_per_run(vms: int, hours: float, p: Provider) -> float:
    return run_matchups(vms, hours, p) / MATCHUPS_PER_TRIAL


def run_cost_with_preemption(vms: int, hours: float, p: Provider) -> float:
    """Cost inflated by expected preemption reruns.

    Model: p.preemption_rate of VM-hrs are lost and rerun.
    Rerun cost = base × preemption_rate.
    """
    return run_cost(vms, hours, p) * (1.0 + p.preemption_rate)


def fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def fmt_usd_fine(x: float) -> str:
    """4-decimal for small-unit costs like $/matchup."""
    return f"${x:.4f}"


# ---------- Scenario: sampler benchmark (2 hulls × 3 samplers × 1 hr) ----------

def benchmark_scenario(p: Provider) -> dict:
    """
    Per-hull benchmark: TPE-24 + CatCMAwM-24 + CatCMAwM-48 at 1 hr each.
    Hybrid dropped (1 hr doesn't give the TPE-exploit stage room).
    """
    runs = [
        # (label, workers, vms, hours)
        ("TPE-24",        24, 12, 1.0),
        ("CatCMAwM-24",   24, 12, 1.0),
        ("CatCMAwM-48",   48, 24, 1.0),
    ]
    n_hulls = 2

    per_run = []
    for label, workers, vms, hrs in runs:
        cost = run_cost_with_preemption(vms, hrs, p)
        trials = trials_per_run(vms, hrs, p)
        per_run.append({
            "label": label,
            "workers": workers,
            "vms": vms,
            "hours": hrs,
            "cost_one_hull": cost,
            "trials_one_hull": trials,
            "cost_n_hulls": cost * n_hulls,
            "trials_n_hulls": trials,  # per hull; n hulls = n independent runs
        })

    total_cost = sum(r["cost_n_hulls"] for r in per_run)
    total_vm_hrs = sum(r["vms"] * r["hours"] * n_hulls for r in per_run)
    # Wall-clock: all 3 samplers run concurrently per hull; 2 hulls can also run concurrently
    # if VM count fits. At peak: 3 samplers × (12+12+24)=48 VMs/hull × 2 hulls = 96 VMs → fits.
    wall_clock_hrs = max(r["hours"] for r in per_run)  # 1 hr

    return {
        "n_hulls": n_hulls,
        "per_run": per_run,
        "total_cost": total_cost,
        "total_vm_hrs": total_vm_hrs,
        "wall_clock_hrs": wall_clock_hrs,
        "peak_vms": 48 * n_hulls,  # if fully parallel
    }


# ---------- Scenario: Phase 7 prep campaign (8 hulls × 600 trials) ----------

def prep_scenario(
    p: Provider,
    n_hulls: int = 8,
    trials_per_study: int = 600,
    workers_per_study: int = 24,
) -> dict:
    """Main Phase 7 prep campaign: 8 hulls × early × 1 seed × N trials."""
    vms_per_study = workers_per_study // JVMS_PER_VM
    matchups_per_study = trials_per_study * MATCHUPS_PER_TRIAL
    hours_per_study = matchups_per_study / (vms_per_study * p.matchups_per_hr_per_vm)

    total_vms = n_hulls * vms_per_study   # all studies concurrent
    total_vm_hrs = n_hulls * vms_per_study * hours_per_study
    total_cost = run_cost_with_preemption(total_vms, hours_per_study, p)

    return {
        "n_hulls": n_hulls,
        "workers_per_study": workers_per_study,
        "vms_per_study": vms_per_study,
        "trials_per_study": trials_per_study,
        "hours_per_study": hours_per_study,
        "wall_clock_hrs": hours_per_study,  # parallel
        "total_vms": total_vms,
        "total_vm_hrs": total_vm_hrs,
        "total_cost": total_cost,
    }


# ---------- Budget rollup ----------

def budget_rollup(p: Provider) -> dict:
    # Probe: 2 VMs × 2 regions × 15 min = 1 VM-hr (throwaway, AMI validation + region health)
    probe_cost = 2 * 2 * 0.25 * p.vm_hourly_usd
    # Smoke: 1 study × 8 workers (4 VMs) × ~2 hr = 8 VM-hr
    # Validates Redis protocol, study.db rsync, cost ledger, teardown, preemption replay.
    smoke_cost = 8 * p.vm_hourly_usd

    bench = benchmark_scenario(p)
    prep = prep_scenario(p)

    slack_reserve = 5.00  # float for retry/experiments
    subtotal = probe_cost + smoke_cost + bench["total_cost"] + prep["total_cost"]
    recommended_budget = subtotal + slack_reserve
    # Round up to nearest $5
    recommended_budget = 5 * ((int(recommended_budget) // 5) + 1)

    return {
        "probe": probe_cost,
        "smoke": smoke_cost,
        "benchmark": bench["total_cost"],
        "prep": prep["total_cost"],
        "subtotal": subtotal,
        "slack": slack_reserve,
        "recommended_budget": recommended_budget,
    }


# ---------- Pretty printing ----------

def print_provider_summary(p: Provider) -> None:
    print(f"## Provider: {p.name}")
    print(f"- VM price: {fmt_usd(p.vm_hourly_usd)}/hr")
    print(f"- Throughput: {p.matchups_per_hr_per_vm:.1f} matchups/hr/VM")
    print(f"- $/matchup: {fmt_usd_fine(cost_per_matchup(p))}")
    print(f"- Preemption rate: {p.preemption_rate*100:.1f}%")
    print()


def print_capacity() -> None:
    print("## AWS quota (2026-04-18 audit)")
    print(f"| Region | Spot vCPU | 8-vCPU VMs |")
    print(f"|---|---|---|")
    total_vcpu = 0
    total_vms = 0
    for region, vcpu in AWS_SPOT_VCPU_BY_REGION.items():
        vms = vcpu // VCPU_PER_VM
        print(f"| {region} | {vcpu} | {vms} |")
        total_vcpu += vcpu
        total_vms += vms
    print(f"| **Total** | **{total_vcpu}** | **{total_vms}** |")
    ne = aws_spot_vm_capacity(["us-east-1", "us-east-2"])
    print(f"\nUs-east-1 + us-east-2 alone: **{ne} VMs** (covers the 96-VM prep target with slack)")
    print()


def print_benchmark(p: Provider) -> None:
    b = benchmark_scenario(p)
    print(f"## Sampler benchmark — {b['n_hulls']} hulls × 3 samplers × {b['per_run'][0]['hours']} hr each")
    print()
    print(f"| Sampler | Workers | VMs | hrs | Trials/run | Cost/hull | Cost ({b['n_hulls']} hulls) |")
    print(f"|---|---|---|---|---|---|---|")
    for r in b["per_run"]:
        print(
            f"| {r['label']} | {r['workers']} | {r['vms']} | {r['hours']:.1f} "
            f"| {r['trials_one_hull']:.0f} | {fmt_usd(r['cost_one_hull'])} | {fmt_usd(r['cost_n_hulls'])} |"
        )
    print()
    print(f"Total benchmark cost: **{fmt_usd(b['total_cost'])}** "
          f"({b['total_vm_hrs']:.0f} VM-hrs, ~{b['wall_clock_hrs']:.1f} hr wall-clock at peak {b['peak_vms']} VMs)")
    print()


def print_prep(p: Provider) -> None:
    pr = prep_scenario(p)
    print(f"## Phase 7 prep campaign — {pr['n_hulls']} hulls × early × {pr['trials_per_study']} trials")
    print()
    print(f"- Workers per study: {pr['workers_per_study']} ({pr['vms_per_study']} VMs × {JVMS_PER_VM} JVMs)")
    print(f"- Wall-clock per study: {pr['hours_per_study']:.2f} hr (studies run in parallel)")
    print(f"- Total VMs at peak: {pr['total_vms']}")
    print(f"- Total VM-hours: {pr['total_vm_hrs']:.1f}")
    print(f"- Cost (incl {p.preemption_rate*100:.0f}% preemption reruns): **{fmt_usd(pr['total_cost'])}**")
    print()


def print_budget(p: Provider) -> None:
    b = budget_rollup(p)
    print(f"## Budget rollup")
    print()
    print(f"| Line item | Cost |")
    print(f"|---|---|")
    print(f"| Validation probe (2 VMs × 2 regions × 15 min) | {fmt_usd(b['probe'])} |")
    print(f"| Pipeline smoke (1 study × 8 workers × 2 hr) | {fmt_usd(b['smoke'])} |")
    print(f"| Sampler benchmark (2h × 3 samplers × 2 hulls) | {fmt_usd(b['benchmark'])} |")
    print(f"| Prep campaign (8 hulls × 600 trials) | {fmt_usd(b['prep'])} |")
    print(f"| **Subtotal** | **{fmt_usd(b['subtotal'])}** |")
    print(f"| Slack (rerun, retries, headroom) | {fmt_usd(b['slack'])} |")
    print(f"| **Recommended budget (rounded up to $5)** | **{fmt_usd(b['recommended_budget'])}** |")
    print()


def sensitivity_analysis() -> None:
    print(f"## Sensitivity: prep cost vs trials_per_study")
    print()
    print(f"| Trials/study | Hrs/study | Cost (AWS, incl preemption) |")
    print(f"|---|---|---|")
    for trials in [400, 500, 600, 700, 800]:
        pr = prep_scenario(AWS_C7A_SPOT, trials_per_study=trials)
        print(f"| {trials} | {pr['hours_per_study']:.2f} | {fmt_usd(pr['total_cost'])} |")
    print()

    print(f"## Sensitivity: prep cost vs n_hulls (at 600 trials)")
    print()
    print(f"| Hulls | Cost (AWS) |")
    print(f"|---|---|")
    for hulls in [4, 6, 8, 10, 12]:
        pr = prep_scenario(AWS_C7A_SPOT, n_hulls=hulls, trials_per_study=600)
        print(f"| {hulls} | {fmt_usd(pr['total_cost'])} |")
    print()


def main() -> None:
    print("# Phase 6 cost model output (2026-04-18)")
    print()
    print_provider_summary(AWS_C7A_SPOT)
    print_provider_summary(HETZNER_CCX33)
    print_capacity()
    print_benchmark(AWS_C7A_SPOT)
    print_prep(AWS_C7A_SPOT)
    print_budget(AWS_C7A_SPOT)
    sensitivity_analysis()


if __name__ == "__main__":
    main()
