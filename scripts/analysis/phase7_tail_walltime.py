"""Phase 7 learned-batch tail-walltime analysis — chart + headline producer.

Closes the roadmap AWS action item "measure learned-batch tail-job walltime
at scale (gates fleet teardown / scale-down-on-drain)". Reads the attempt-3
batch directory (`data/phase7/learned_surrogate_batch_v2_2026-07/`):

  - ``ledger.jsonl``       — per-worker cost heartbeats (fleet window, $/hr)
  - ``events/*.jsonl``     — job → worker assignment (no timestamps)
  - ``results/*.json``     — per-job ``elapsed_seconds``

and reconstructs per-worker busy time, the fleet drain curve, idle-tail
hours/cost, and an LPT (longest-processing-time-first) counterfactual
makespan across fleet sizes. Per-job events carry no timestamps, so worker
finish times are estimated as ``fleet_start + setup + Σ elapsed`` — see the
report Methods for why mid-run idle is negligible.

Outputs:

  data/phase7-tail-walltime/charts/01_drain_curve.png
  data/phase7-tail-walltime/charts/02_job_walltime_by_cell.png
  data/phase7-tail-walltime/headline_numbers.json

... where `headline_numbers.json` carries the exact values cited by
`docs/reports/2026-07-12-phase7-tail-walltime.md`.

Run: `uv run python scripts/analysis/phase7_tail_walltime.py`
"""

from __future__ import annotations

import heapq
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --- publication-quality matplotlib defaults (aligned with sibling producers) ---
plt.rcParams.update(
    {
        "figure.dpi": 110,
        "savefig.dpi": 200,
        "figure.constrained_layout.use": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": "#dddddd",
        "grid.linewidth": 0.6,
        "legend.frameon": False,
        "axes.prop_cycle": plt.cycler(
            color=[
                "#006BA4",
                "#FF800E",
                "#ABABAB",
                "#595959",
                "#5F9ED1",
                "#C85200",
                "#898989",
                "#A2C8EC",
                "#FFBC79",
                "#CFCFCF",
            ]
        ),
    }
)

logger = logging.getLogger(__name__)

BATCH_DIR = Path("data/phase7/learned_surrogate_batch_v2_2026-07")
OUT_DIR = Path("data/phase7-tail-walltime")
COUNTERFACTUAL_FLEET_SIZES = (18, 24, 36, 48)


@dataclass(frozen=True)
class Job:
    job_id: str
    split: str
    model: str
    worker: str
    elapsed_seconds: float


@dataclass(frozen=True)
class FleetWindow:
    start: datetime
    end: datetime
    n_workers: int
    cumulative_usd: float

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0

    @property
    def capacity_hours(self) -> float:
        return self.n_workers * self.hours

    @property
    def effective_usd_per_worker_hour(self) -> float:
        return self.cumulative_usd / self.capacity_hours


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def load_fleet_window(ledger_path: Path) -> FleetWindow:
    """Fleet window + effective rate from the cost-heartbeat ledger."""
    timestamps: list[datetime] = []
    workers: set[str] = set()
    cumulative = 0.0
    with ledger_path.open() as fh:
        for line in fh:
            beat = json.loads(line)
            timestamps.append(_parse_ts(beat["timestamp"]))
            workers.add(beat["worker_id"])
            cumulative = max(cumulative, beat["cumulative_usd"])
    return FleetWindow(
        start=min(timestamps),
        end=max(timestamps),
        n_workers=len(workers),
        cumulative_usd=cumulative,
    )


def load_jobs(batch_dir: Path) -> list[Job]:
    """Join job → worker (events) with job → elapsed (result artifacts)."""
    jobs: list[Job] = []
    for event_path in sorted((batch_dir / "events").glob("*.jsonl")):
        job_id = event_path.stem
        worker = ""
        with event_path.open() as fh:
            for line in fh:
                event = json.loads(line)
                if event.get("event") == "experiment_start":
                    worker = event["instance_id"]
        if not worker:
            logger.warning("job %s has no experiment_start event; skipping", job_id)
            continue
        result_path = batch_dir / "results" / f"{job_id}.json"
        with result_path.open() as fh:
            elapsed = float(json.load(fh)["elapsed_seconds"])
        split, model = job_id.split("__")[:2]
        jobs.append(Job(job_id, split, model, worker, elapsed))
    return jobs


def per_worker_busy_hours(jobs: list[Job]) -> dict[str, float]:
    busy: dict[str, float] = {}
    for job in jobs:
        busy[job.worker] = busy.get(job.worker, 0.0) + job.elapsed_seconds / 3600.0
    return busy


def lpt_makespan_hours(durations_hours: list[float], n_workers: int) -> float:
    """Greedy longest-processing-time-first schedule makespan."""
    loads = [0.0] * n_workers
    heapq.heapify(loads)
    for duration in sorted(durations_hours, reverse=True):
        heapq.heappush(loads, heapq.heappop(loads) + duration)
    return max(loads)


def quantile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank quantile on a pre-sorted list."""
    index = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[index]


def chart_drain_curve(
    busy: dict[str, float],
    window: FleetWindow,
    setup_hours: float,
    out_path: Path,
) -> None:
    finish_times = sorted(setup_hours + hours for hours in busy.values())
    # Step function: workers still busy at time t.
    xs: list[float] = [0.0]
    ys: list[int] = [len(finish_times)]
    for i, finish in enumerate(finish_times):
        xs.extend([finish, finish])
        ys.extend([len(finish_times) - i, len(finish_times) - i - 1])
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(xs, ys, color="#006BA4", linewidth=1.6, label="workers busy (estimated)")
    ax.fill_between(xs, ys, [len(finish_times)] * len(ys), step="pre", alpha=0.15, color="#FF800E")
    ax.axvline(window.hours, color="#595959", linestyle="--", linewidth=1.0)
    ax.annotate(
        f"fleet teardown {window.hours:.2f} h",
        xy=(window.hours, len(finish_times) * 0.5),
        xytext=(-8, 0),
        textcoords="offset points",
        rotation=90,
        va="center",
        ha="right",
        fontsize=8,
        color="#595959",
    )
    ax.set_xlabel("hours since fleet launch (h)")
    ax.set_ylabel("workers busy (count)")
    ax.set_title("Attempt-3 fleet drain — estimated busy workers over time")
    ax.set_ylim(0, len(finish_times) + 1)
    ax.legend(loc="lower left")
    fig.savefig(out_path)
    plt.close(fig)


def chart_job_walltime_by_cell(jobs: list[Job], out_path: Path) -> None:
    cells = sorted({(job.split, job.model) for job in jobs})
    labels = [f"{split} × {model}" for split, model in cells]
    data = [
        [job.elapsed_seconds / 60.0 for job in jobs if (job.split, job.model) == cell]
        for cell in cells
    ]
    fig, ax = plt.subplots(figsize=(7.2, 0.32 * len(cells) + 1.6))
    ax.boxplot(
        data,
        vert=False,
        tick_labels=[
            f"{label} (n={len(values)})" for label, values in zip(labels, data, strict=True)
        ],
        widths=0.6,
        medianprops={"color": "#C85200"},
        flierprops={"markersize": 3},
    )
    ax.set_xlabel("job walltime (minutes)")
    ax.set_title("Attempt-3 job walltime by split × model family")
    ax.tick_params(axis="y", labelsize=7)
    fig.savefig(out_path)
    plt.close(fig)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    charts_dir = OUT_DIR / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    window = load_fleet_window(BATCH_DIR / "ledger.jsonl")
    jobs = load_jobs(BATCH_DIR)
    busy = per_worker_busy_hours(jobs)

    busy_values = sorted(busy.values())
    busy_total = sum(busy_values)
    # Residual of the makespan worker bounds setup + per-job overheads from
    # above (that worker has no tail idle); use it as the common setup estimate.
    setup_hours = window.hours - busy_values[-1]
    rate = window.effective_usd_per_worker_hour
    idle_tail_hours = window.capacity_hours - busy_total - window.n_workers * setup_hours
    idle_upper_hours = window.capacity_hours - busy_total

    durations = sorted((job.elapsed_seconds / 3600.0 for job in jobs), reverse=True)
    lpt_by_fleet = {
        n: lpt_makespan_hours(durations, n) + setup_hours for n in COUNTERFACTUAL_FLEET_SIZES
    }
    lower_bound_hours = max(busy_total / window.n_workers, durations[0]) + setup_hours

    per_cell: dict[str, dict[str, float | int]] = {}
    for cell in sorted({(job.split, job.model) for job in jobs}):
        values = sorted(
            job.elapsed_seconds / 60.0 for job in jobs if (job.split, job.model) == cell
        )
        per_cell[f"{cell[0]}__{cell[1]}"] = {
            "n": len(values),
            "min_minutes": values[0],
            "median_minutes": quantile(values, 0.5),
            "max_minutes": values[-1],
        }

    elapsed_minutes = sorted(job.elapsed_seconds / 60.0 for job in jobs)
    headline = {
        "batch_dir": str(BATCH_DIR),
        "n_jobs": len(jobs),
        "n_workers": window.n_workers,
        "fleet_start_utc": window.start.isoformat(),
        "fleet_end_utc": window.end.isoformat(),
        "fleet_window_hours": window.hours,
        "capacity_worker_hours": window.capacity_hours,
        "busy_worker_hours": busy_total,
        "utilization": busy_total / window.capacity_hours,
        "setup_overhead_hours_bound": setup_hours,
        "effective_usd_per_worker_hour": rate,
        "cumulative_usd": window.cumulative_usd,
        "idle_tail_worker_hours": idle_tail_hours,
        "idle_tail_usd": idle_tail_hours * rate,
        "idle_upper_worker_hours": idle_upper_hours,
        "idle_upper_usd": idle_upper_hours * rate,
        "per_worker_busy_hours": {
            "min": busy_values[0],
            "p25": quantile(busy_values, 0.25),
            "median": quantile(busy_values, 0.5),
            "p75": quantile(busy_values, 0.75),
            "max": busy_values[-1],
        },
        "job_walltime_minutes": {
            "min": elapsed_minutes[0],
            "p50": quantile(elapsed_minutes, 0.50),
            "p90": quantile(elapsed_minutes, 0.90),
            "p99": quantile(elapsed_minutes, 0.99),
            "max": elapsed_minutes[-1],
        },
        "longest_jobs": [
            {
                "job_id": job.job_id,
                "minutes": job.elapsed_seconds / 60.0,
            }
            for job in sorted(jobs, key=lambda j: -j.elapsed_seconds)[:10]
        ],
        "lpt_makespan_hours_by_fleet_size": lpt_by_fleet,
        "makespan_lower_bound_hours_36": lower_bound_hours,
        "per_cell_walltime_minutes": per_cell,
    }
    (OUT_DIR / "headline_numbers.json").write_text(json.dumps(headline, indent=2) + "\n")

    chart_drain_curve(busy, window, setup_hours, charts_dir / "01_drain_curve.png")
    chart_job_walltime_by_cell(jobs, charts_dir / "02_job_walltime_by_cell.png")

    logger.info(
        "window %.2f h × %d workers; busy %.1f h (utilization %.1f%%); "
        "idle tail %.1f h ≈ $%.2f of $%.2f",
        window.hours,
        window.n_workers,
        busy_total,
        100 * busy_total / window.capacity_hours,
        idle_tail_hours,
        idle_tail_hours * rate,
        window.cumulative_usd,
    )


if __name__ == "__main__":
    main()
