"""Throughput estimator for combat simulation campaigns.

Computes wall-clock time and cost estimates given search space statistics,
simulation parameters, and cloud provider pricing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import GameData, HullSize, ShipHull
from .search_space import build_search_space


@dataclass(frozen=True)
class CloudProvider:
    name: str
    cost_per_hour: float  # USD
    max_instances: int  # game instances per machine


DEFAULT_PROVIDERS: list[CloudProvider] = [
    CloudProvider("Hetzner CCX43", cost_per_hour=0.22, max_instances=8),
    CloudProvider("Hetzner CCX53", cost_per_hour=0.40, max_instances=16),
    CloudProvider("AWS c7i.4xl spot", cost_per_hour=0.25, max_instances=8),
]


@dataclass(frozen=True)
class HullSpaceStats:
    hull_id: str
    hull_name: str
    hull_size: HullSize
    num_slots: int
    options_per_slot: list[int]
    weapon_combinations: int
    num_eligible_hullmods: int
    max_vents: int
    max_capacitors: int


@dataclass(frozen=True)
class SimulationParams:
    time_mult: float = 3.0
    game_time_limit_seconds: float = 180.0
    startup_seconds: float = 35.0
    batch_size: int = 50
    num_instances: int = 1
    sims_per_hull: int = 1000
    num_hulls: int = 50
    providers: list[CloudProvider] = field(default_factory=lambda: list(DEFAULT_PROVIDERS))


@dataclass(frozen=True)
class ThroughputEstimate:
    wall_seconds_per_matchup: float
    matchups_per_hour_per_instance: float
    startup_overhead_fraction: float
    effective_matchups_per_hour: float
    total_sims: int
    total_hours: float
    cost_estimates: dict[str, float]  # provider name → USD


def compute_hull_space_stats(hull: ShipHull, game_data: GameData) -> HullSpaceStats:
    """Compute search space statistics for a single hull."""
    space = build_search_space(hull, game_data)

    options_per_slot = [len(opts) for opts in space.weapon_options.values()]
    weapon_combinations = math.prod(options_per_slot) if options_per_slot else 1

    return HullSpaceStats(
        hull_id=hull.id,
        hull_name=hull.name,
        hull_size=hull.hull_size,
        num_slots=len(space.weapon_options),
        options_per_slot=options_per_slot,
        weapon_combinations=weapon_combinations,
        num_eligible_hullmods=len(space.eligible_hullmods),
        max_vents=hull.max_vents,
        max_capacitors=hull.max_capacitors,
    )


def compute_all_hull_stats(game_data: GameData) -> list[HullSpaceStats]:
    """Compute search space statistics for all hulls, sorted by weapon_combinations descending."""
    stats = [compute_hull_space_stats(h, game_data) for h in game_data.hulls.values()]
    stats.sort(key=lambda s: s.weapon_combinations, reverse=True)
    return stats


def estimate_throughput(params: SimulationParams | None = None) -> ThroughputEstimate:
    """Estimate wall-clock time and cost for a simulation campaign."""
    if params is None:
        params = SimulationParams()

    wall_seconds = params.game_time_limit_seconds / params.time_mult
    matchups_per_hour = 3600.0 / wall_seconds

    batch_wall_time = params.startup_seconds + params.batch_size * wall_seconds
    startup_fraction = params.startup_seconds / batch_wall_time

    effective_per_instance = matchups_per_hour * (1.0 - startup_fraction)
    effective_total = effective_per_instance * params.num_instances

    total_sims = params.sims_per_hull * params.num_hulls
    total_hours = total_sims / effective_total

    cost_estimates: dict[str, float] = {}
    for provider in params.providers:
        num_machines = math.ceil(params.num_instances / provider.max_instances)
        cost_estimates[provider.name] = total_hours * num_machines * provider.cost_per_hour

    return ThroughputEstimate(
        wall_seconds_per_matchup=wall_seconds,
        matchups_per_hour_per_instance=matchups_per_hour,
        startup_overhead_fraction=startup_fraction,
        effective_matchups_per_hour=effective_total,
        total_sims=total_sims,
        total_hours=total_hours,
        cost_estimates=cost_estimates,
    )


def _fmt_combinations(n: int) -> str:
    """Format large combination counts readably."""
    if n < 1_000_000:
        return f"{n:,}"
    exp = math.floor(math.log10(n))
    mantissa = n / (10 ** exp)
    return f"{mantissa:.1f}e{exp}"


def format_estimate_report(
    hull_stats: list[HullSpaceStats],
    estimate: ThroughputEstimate,
) -> str:
    """Format a human-readable estimation report."""
    lines: list[str] = []

    # --- Search Space Summary ---
    lines.append("=" * 70)
    lines.append("Search Space Summary")
    lines.append("=" * 70)
    lines.append(f"{'Hull':<25} {'Size':<12} {'Slots':>5} {'Hullmods':>8} {'Weapon Combos':>16}")
    lines.append("-" * 70)
    for s in hull_stats[:30]:  # top 30 by combinations
        lines.append(
            f"{s.hull_name:<25} {s.hull_size.value:<12} {s.num_slots:>5} "
            f"{s.num_eligible_hullmods:>8} {_fmt_combinations(s.weapon_combinations):>16}"
        )
    if len(hull_stats) > 30:
        lines.append(f"  ... and {len(hull_stats) - 30} more hulls")
    lines.append("")

    total_combos = sum(s.weapon_combinations for s in hull_stats)
    lines.append(f"Total hulls: {len(hull_stats)}")
    lines.append(f"Total weapon combinations across all hulls: {_fmt_combinations(total_combos)}")
    lines.append("")

    # --- Throughput Estimate ---
    lines.append("=" * 70)
    lines.append("Throughput Estimate")
    lines.append("=" * 70)
    lines.append(f"Wall-clock per matchup:        {estimate.wall_seconds_per_matchup:.1f}s")
    lines.append(f"Matchups/hr/instance:          {estimate.matchups_per_hour_per_instance:.0f}")
    lines.append(f"Startup overhead:              {estimate.startup_overhead_fraction:.1%}")
    lines.append(f"Effective matchups/hr (total):  {estimate.effective_matchups_per_hour:.0f}")
    lines.append(f"Total simulations:             {estimate.total_sims:,}")
    lines.append(f"Total wall-clock time:         {estimate.total_hours:.1f} hours")
    lines.append("")

    # --- Cost Estimates ---
    lines.append("=" * 70)
    lines.append("Cost Estimates")
    lines.append("=" * 70)
    lines.append(f"{'Provider':<25} {'$/hr':>8} {'Machines':>8} {'Total $':>10}")
    lines.append("-" * 55)
    for name, cost in sorted(estimate.cost_estimates.items(), key=lambda x: x[1]):
        provider = next((p for p in DEFAULT_PROVIDERS if p.name == name), None)
        if provider:
            machines = math.ceil(estimate.total_sims
                                 / (estimate.effective_matchups_per_hour * estimate.total_hours)
                                 / provider.max_instances) if provider.max_instances > 0 else 1
            # Simpler: just compute from num_instances in the estimate
            lines.append(f"{name:<25} ${provider.cost_per_hour:>6.2f} {'':>8} ${cost:>9.2f}")
        else:
            lines.append(f"{name:<25} {'':>8} {'':>8} ${cost:>9.2f}")
    lines.append("")

    return "\n".join(lines)


def print_scenario_comparison(num_hulls: int = 50) -> str:
    """Print a comparison of different simulation configurations."""
    lines: list[str] = []
    lines.append("=" * 90)
    lines.append("Scenario Comparison")
    lines.append("=" * 90)
    lines.append(
        f"{'Scenario':<35} {'Per-match':>9} {'Eff/hr':>8} "
        f"{'Hours':>7} {'Hetzner43':>10} {'AWS spot':>10}"
    )
    lines.append("-" * 90)

    scenarios = [
        ("3x speed, 180s limit, 1 inst", dict(time_mult=3.0, game_time_limit_seconds=180, num_instances=1)),
        ("3x speed, 180s limit, 8 inst", dict(time_mult=3.0, game_time_limit_seconds=180, num_instances=8)),
        ("5x speed, 180s limit, 8 inst", dict(time_mult=5.0, game_time_limit_seconds=180, num_instances=8)),
        ("5x speed, 180s limit, 16 inst", dict(time_mult=5.0, game_time_limit_seconds=180, num_instances=16)),
        ("5x speed, 120s limit, 8 inst", dict(time_mult=5.0, game_time_limit_seconds=120, num_instances=8)),
        ("5x speed, 120s limit, 16 inst", dict(time_mult=5.0, game_time_limit_seconds=120, num_instances=16)),
        ("5x speed, 60s limit, 8 inst", dict(time_mult=5.0, game_time_limit_seconds=60, num_instances=8)),
        ("5x speed, 60s limit, 16 inst", dict(time_mult=5.0, game_time_limit_seconds=60, num_instances=16)),
    ]

    providers = [
        CloudProvider("Hetzner CCX43", cost_per_hour=0.22, max_instances=8),
        CloudProvider("AWS c7i.4xl spot", cost_per_hour=0.25, max_instances=8),
    ]

    for label, overrides in scenarios:
        p = SimulationParams(
            sims_per_hull=1000, num_hulls=num_hulls, providers=providers, **overrides,
        )
        e = estimate_throughput(p)
        h43 = e.cost_estimates.get("Hetzner CCX43", 0)
        aws = e.cost_estimates.get("AWS c7i.4xl spot", 0)
        lines.append(
            f"{label:<35} {e.wall_seconds_per_matchup:>7.1f}s "
            f"{e.effective_matchups_per_hour:>7.0f} "
            f"{e.total_hours:>6.1f}h "
            f"${h43:>8.2f} ${aws:>8.2f}"
        )

    lines.append("")
    lines.append(f"All scenarios: {num_hulls} hulls x 1000 sims/hull = {num_hulls * 1000:,} total sims")
    lines.append("Startup: 35s per instance launch, batch size 50")
    lines.append("")
    return "\n".join(lines)


def budget_optimizer(
    budget_usd: float,
    num_hulls: int = 118,
    sims_per_hull: int = 1000,
) -> str:
    """Find the fastest configuration within a dollar budget.

    Searches over provider × time_mult × game_time_limit × num_instances
    to minimize wall-clock hours while staying under budget.
    """
    lines: list[str] = []
    lines.append("=" * 95)
    lines.append(f"Budget Optimization: ${budget_usd:.0f} for {num_hulls} hulls x {sims_per_hull} sims/hull"
                 f" = {num_hulls * sims_per_hull:,} sims")
    lines.append("=" * 95)

    providers = [
        CloudProvider("Hetzner CCX43", cost_per_hour=0.22, max_instances=8),
        CloudProvider("Hetzner CCX53", cost_per_hour=0.40, max_instances=16),
        CloudProvider("AWS c7i.4xl spot", cost_per_hour=0.25, max_instances=8),
    ]

    time_mults = [3.0, 5.0]
    game_limits = [60, 120, 180]
    instance_counts = [1, 2, 4, 8, 16, 24, 32, 48, 64, 96, 128]

    results: list[tuple[float, float, str, SimulationParams, ThroughputEstimate]] = []

    for provider in providers:
        for tm in time_mults:
            for gl in game_limits:
                for ni in instance_counts:
                    p = SimulationParams(
                        time_mult=tm,
                        game_time_limit_seconds=gl,
                        num_instances=ni,
                        sims_per_hull=sims_per_hull,
                        num_hulls=num_hulls,
                        batch_size=50,
                        providers=[provider],
                    )
                    e = estimate_throughput(p)
                    cost = e.cost_estimates[provider.name]
                    if cost <= budget_usd:
                        results.append((e.total_hours, cost, provider.name, p, e))

    results.sort(key=lambda r: r[0])  # sort by hours (fastest first)

    lines.append("")
    lines.append(
        f"{'Provider':<20} {'Speed':>5} {'Limit':>5} {'Inst':>4} "
        f"{'Machines':>8} {'Hours':>7} {'Cost':>8} {'$/inst/hr':>9}"
    )
    lines.append("-" * 95)

    seen_providers: set[str] = set()
    for hours, cost, pname, params, est in results[:20]:
        provider = next(p for p in providers if p.name == pname)
        machines = math.ceil(params.num_instances / provider.max_instances)
        cost_per_inst_hr = provider.cost_per_hour / provider.max_instances
        lines.append(
            f"{pname:<20} {params.time_mult:>4.0f}x {params.game_time_limit_seconds:>4.0f}s "
            f"{params.num_instances:>4} {machines:>8} "
            f"{hours:>6.1f}h ${cost:>6.2f} ${cost_per_inst_hr:>.4f}"
        )
        seen_providers.add(pname)

    if not results:
        lines.append("No configuration fits within budget!")

    lines.append("")

    # Best per provider
    lines.append("--- Best per provider ---")
    for pname in ["Hetzner CCX43", "Hetzner CCX53", "AWS c7i.4xl spot"]:
        provider_results = [r for r in results if r[2] == pname]
        if provider_results:
            hours, cost, _, params, est = provider_results[0]
            provider = next(p for p in providers if p.name == pname)
            machines = math.ceil(params.num_instances / provider.max_instances)
            lines.append(
                f"  {pname}: {params.num_instances} instances on {machines} machine(s), "
                f"{params.time_mult:.0f}x speed, {params.game_time_limit_seconds:.0f}s limit "
                f"→ {hours:.1f}h, ${cost:.2f}"
            )

    lines.append("")
    return "\n".join(lines)

