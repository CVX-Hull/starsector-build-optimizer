"""Phase 7 prior-run matchup recovery and derived SQLite materialization."""

from __future__ import annotations

import json
import math
import random
import re
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .game_manifest import GameManifest
from .honest_evaluator import extract_top_builds
from .models import Build, GameData, ShipHull
from .optimizer import trial_params_to_build
from .repair import is_feasible, repair_build

BUILD_KEY_HEX_LENGTH = 16


class BuildSourceKind(StrEnum):
    EXACT_LOGGED_BUILD = "exact_logged_build"
    DB_RECONSTRUCTED_BUILD = "db_reconstructed_build"
    HONEST_EVAL_CANDIDATE_BUILD = "honest_eval_candidate_build"
    HONEST_EVAL_OUTPUT_BUILD = "honest_eval_output_build"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class RecoveredBuild:
    build_key: str
    build: Build
    source_kind: BuildSourceKind
    campaign: str | None
    study: str | None
    seed: int | None
    rank: int | None
    trial_number: int | None
    score: float | None
    source_path: str


@dataclass(frozen=True)
class TrainingMatchupRow:
    source_path: str
    campaign: str | None
    seed: int | None
    trial_number: int
    build_key: str
    opponent_variant_id: str
    opponent_index: int
    target: float
    row_kind: str


@dataclass(frozen=True)
class HonestEvalMatchupRow:
    source_path: str
    build_id: str
    build_key: str | None
    opponent_variant_id: str
    replicate_idx: int
    target: float


@dataclass(frozen=True)
class SplitIds:
    train: tuple[TrainingMatchupRow | HonestEvalMatchupRow, ...]
    test: tuple[TrainingMatchupRow | HonestEvalMatchupRow, ...]


def _canonical_build_dict(build: Build) -> dict[str, Any]:
    return {
        "hull_id": build.hull_id,
        "weapon_assignments": {
            key: value for key, value in sorted(build.weapon_assignments.items())
        },
        "hullmods": sorted(build.hullmods),
        "flux_vents": int(build.flux_vents),
        "flux_capacitors": int(build.flux_capacitors),
    }


def _build_from_canonical(data: Mapping[str, Any]) -> Build:
    return Build(
        hull_id=str(data["hull_id"]),
        weapon_assignments=dict(data["weapon_assignments"]),
        hullmods=frozenset(str(item) for item in data["hullmods"]),
        flux_vents=int(data["flux_vents"]),
        flux_capacitors=int(data["flux_capacitors"]),
    )


def _build_json(build: Build) -> str:
    return json.dumps(_canonical_build_dict(build), sort_keys=True, separators=(",", ":"))


def build_key(build: Build) -> str:
    """Stable hash over canonical build JSON."""
    return sha256(_build_json(build).encode("utf-8")).hexdigest()[:BUILD_KEY_HEX_LENGTH]


def build_from_log_row(row: Mapping[str, Any]) -> Build:
    raw = row["build"]
    return Build(
        hull_id=str(raw["hull_id"]),
        weapon_assignments=dict(raw["weapon_assignments"]),
        hullmods=frozenset(str(item) for item in raw["hullmods"]),
        flux_vents=int(raw["flux_vents"]),
        flux_capacitors=int(raw["flux_capacitors"]),
    )


def _path_campaign(path: Path) -> str | None:
    parts = list(path.parts)
    if "logs" in parts:
        idx = parts.index("logs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "__seed" in path.parent.name:
        return path.parent.parent.name
    for part in parts:
        if part.startswith("wave"):
            return part
    return None


def _path_seed(path: Path) -> int | None:
    match = re.search(r"__seed(-?\d+)", str(path))
    return int(match.group(1)) if match else None


def _row_kind(row: Mapping[str, Any]) -> str:
    if bool(row.get("invalid_spec")):
        return "invalid_spec"
    if bool(row.get("cache_hit")):
        return "cache_hit"
    if bool(row.get("pruned")):
        return "pruned"
    return "finalized"


def recover_logged_builds(paths: Sequence[Path]) -> tuple[RecoveredBuild, ...]:
    out: list[RecoveredBuild] = []
    seen: set[tuple[str, int | None, str]] = set()
    for path in paths:
        campaign = _path_campaign(path)
        seed = _path_seed(path)
        study = path.parent.name
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not row.get("build") or row.get("invalid_spec"):
                    continue
                build = build_from_log_row(row)
                key = build_key(build)
                trial_number = int(row["trial_number"]) if "trial_number" in row else None
                dedupe = (str(path), trial_number, key)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                out.append(RecoveredBuild(
                    build_key=key,
                    build=build,
                    source_kind=BuildSourceKind.EXACT_LOGGED_BUILD,
                    campaign=campaign,
                    study=study,
                    seed=seed,
                    rank=None,
                    trial_number=trial_number,
                    score=_score_from_log_row(row),
                    source_path=str(path),
                ))
    return tuple(out)


def _score_from_log_row(row: Mapping[str, Any]) -> float | None:
    for key in ("raw_fitness", "eb_fitness", "fitness"):
        value = row.get(key)
        if isinstance(value, int | float) and math.isfinite(float(value)):
            return float(value)
    return None


def _decode_distribution_value(param_value: float, distribution_json: str) -> Any:
    data = json.loads(distribution_json)
    name = data.get("name")
    attrs = data.get("attributes") or {}
    if name == "CategoricalDistribution":
        choices = attrs["choices"]
        idx = int(param_value)
        try:
            return choices[idx]
        except IndexError as exc:
            raise ValueError(
                f"categorical param index {idx} outside choices length {len(choices)}"
            ) from exc
    if name == "IntDistribution":
        return int(param_value)
    if name == "FloatDistribution":
        return float(param_value)
    raise ValueError(f"unsupported Optuna distribution {name!r}")


def _trial_params_from_db(con: sqlite3.Connection, trial_id: int) -> dict[str, Any]:
    rows = con.execute(
        """
        select param_name, param_value, distribution_json
        from trial_params
        where trial_id = ?
        order by param_name
        """,
        (trial_id,),
    ).fetchall()
    return {
        str(name): _decode_distribution_value(float(value), str(distribution_json))
        for name, value, distribution_json in rows
    }


def recover_study_db_builds(
    db_path: Path,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    *,
    campaign: str | None = None,
    study: str | None = None,
    seed: int | None = None,
) -> tuple[RecoveredBuild, ...]:
    con = sqlite3.connect(db_path)
    try:
        trials = con.execute(
            """
            select t.trial_id, t.number, t.state, v.value
            from trials t
            left join trial_values v on t.trial_id = v.trial_id and v.objective = 0
            order by t.number
            """
        ).fetchall()
        out: list[RecoveredBuild] = []
        for trial_id, number, state, value in trials:
            if state not in {"COMPLETE", "PRUNED"}:
                continue
            params = _trial_params_from_db(con, int(trial_id))
            if not params:
                continue
            raw = trial_params_to_build(params, hull.id)
            repaired = repair_build(raw, hull, game_data, manifest)
            feasible, violations = is_feasible(repaired, hull, game_data, manifest)
            if not feasible:
                raise ValueError(
                    f"DB-reconstructed trial {number} from {db_path} is infeasible after repair: {violations}"
                )
            out.append(RecoveredBuild(
                build_key=build_key(repaired),
                build=repaired,
                source_kind=BuildSourceKind.DB_RECONSTRUCTED_BUILD,
                campaign=campaign,
                study=study or db_path.stem,
                seed=seed,
                rank=None,
                trial_number=int(number),
                score=float(value) if value is not None else None,
                source_path=str(db_path),
            ))
        return tuple(out)
    finally:
        con.close()


def iter_training_matchups(paths: Sequence[Path]) -> Iterator[TrainingMatchupRow]:
    for path in paths:
        campaign = _path_campaign(path)
        seed = _path_seed(path)
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not row.get("build") or "trial_number" not in row:
                    continue
                kind = _row_kind(row)
                if kind in {"cache_hit", "invalid_spec"}:
                    continue
                build = build_from_log_row(row)
                for idx, result in enumerate(row.get("opponent_results") or ()):
                    if result.get("hp_differential") is None:
                        continue
                    yield TrainingMatchupRow(
                        source_path=str(path),
                        campaign=campaign,
                        seed=seed,
                        trial_number=int(row["trial_number"]),
                        build_key=build_key(build),
                        opponent_variant_id=str(result["opponent"]),
                        opponent_index=idx,
                        target=float(result["hp_differential"]),
                        row_kind=kind,
                    )


def recover_honest_eval_candidate_builds(
    eval_log_paths: Sequence[Path],
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    *,
    top_k: int,
    method: str = "twfe_eb",
) -> tuple[RecoveredBuild, ...]:
    out: list[RecoveredBuild] = []
    for path in eval_log_paths:
        campaign = _path_campaign(path)
        seed = _path_seed(path)
        study = path.parent.name
        for rank, score, build in extract_top_builds(
            path, hull, game_data, manifest, top_k, method=method,
        ):
            out.append(RecoveredBuild(
                build_key=build_key(build),
                build=build,
                source_kind=BuildSourceKind.HONEST_EVAL_CANDIDATE_BUILD,
                campaign=campaign,
                study=study,
                seed=seed,
                rank=rank,
                trial_number=None,
                score=score,
                source_path=str(path),
            ))
    return tuple(out)


def recover_honest_eval_output_builds(paths: Sequence[Path]) -> tuple[RecoveredBuild, ...]:
    out: list[RecoveredBuild] = []
    for path in paths:
        data = json.loads(path.read_text())
        for row in data.get("evaluated_builds") or ():
            build = _build_from_canonical(row["build"])
            out.append(
                RecoveredBuild(
                    build_key=build_key(build),
                    build=build,
                    source_kind=BuildSourceKind.HONEST_EVAL_OUTPUT_BUILD,
                    campaign=(
                        str(row["source_campaign"])
                        if row.get("source_campaign") is not None
                        else None
                    ),
                    study=(
                        f"s{int(row['source_study_idx'])}"
                        if row.get("source_study_idx") is not None
                        else None
                    ),
                    seed=(
                        int(row["source_seed_idx"])
                        if row.get("source_seed_idx") is not None
                        else None
                    ),
                    rank=int(row["source_rank"]) if row.get("source_rank") is not None else None,
                    trial_number=None,
                    score=(
                        float(row["oracle_score"])
                        if row.get("oracle_score") is not None
                        else None
                    ),
                    source_path=str(path),
                )
            )
    return tuple(out)


def honest_build_id_to_key(candidates: Sequence[RecoveredBuild]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in candidates:
        if item.rank is None or item.seed is None or item.campaign is None:
            continue
        study_idx = 0
        if item.study and item.study.startswith("s") and item.study[1:].isdigit():
            study_idx = int(item.study[1:])
        elif item.study:
            study_match = re.search(r"__s(\d+)__", item.study)
            if study_match:
                study_idx = int(study_match.group(1))
        build_id = f"honest__{item.campaign}__s{study_idx}__seed{item.seed}__rank{item.rank}"
        out[build_id] = item.build_key
    return out


def iter_honest_eval_matchups(
    ledger_path: Path,
    build_id_to_key: Mapping[str, str] | None = None,
) -> Iterator[HonestEvalMatchupRow]:
    mapping = build_id_to_key or {}
    with ledger_path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            build_id = str(row["build_id"])
            yield HonestEvalMatchupRow(
                source_path=str(ledger_path),
                build_id=build_id,
                build_key=mapping.get(build_id),
                opponent_variant_id=str(row["opponent_variant_id"]),
                replicate_idx=int(row["replicate_idx"]),
                target=float(row["fitness"]),
            )


def materialize_sqlite(
    db_path: Path,
    *,
    recovered_builds: Sequence[RecoveredBuild],
    training_matchups: Iterable[TrainingMatchupRow] = (),
    honest_eval_matchups: Iterable[HonestEvalMatchupRow] = (),
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        create table if not exists recovered_builds (
            row_key text primary key,
            build_key text not null,
            source_kind text not null,
            campaign text,
            study text,
            seed integer,
            rank integer,
            trial_number integer,
            score real,
            source_path text not null,
            build_json text not null
        );

        create table if not exists training_matchups (
            source_path text not null,
            campaign text,
            seed integer,
            trial_number integer not null,
            build_key text not null,
            opponent_variant_id text not null,
            opponent_index integer not null,
            target real not null,
            row_kind text not null,
            primary key (source_path, trial_number, opponent_index)
        );

        create table if not exists honest_eval_matchups (
            source_path text not null,
            build_id text not null,
            build_key text,
            opponent_variant_id text not null,
            replicate_idx integer not null,
            target real not null,
            primary key (source_path, build_id, opponent_variant_id, replicate_idx)
        );
        """
    )
    con.execute("delete from recovered_builds")
    con.execute("delete from training_matchups")
    con.execute("delete from honest_eval_matchups")
    con.executemany(
        """
        insert or replace into recovered_builds
        (row_key, build_key, source_kind, campaign, study, seed, rank, trial_number, score, source_path, build_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "|".join((
                    item.build_key,
                    item.source_kind.value,
                    item.source_path,
                    str(item.trial_number if item.trial_number is not None else -1),
                    str(item.rank if item.rank is not None else -1),
                )),
                item.build_key,
                item.source_kind.value,
                item.campaign,
                item.study,
                item.seed,
                item.rank,
                item.trial_number,
                item.score,
                item.source_path,
                _build_json(item.build),
            )
            for item in recovered_builds
        ],
    )
    con.executemany(
        """
        insert or replace into training_matchups
        (source_path, campaign, seed, trial_number, build_key, opponent_variant_id, opponent_index, target, row_kind)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.source_path,
                row.campaign,
                row.seed,
                row.trial_number,
                row.build_key,
                row.opponent_variant_id,
                row.opponent_index,
                row.target,
                row.row_kind,
            )
            for row in training_matchups
        ],
    )
    con.executemany(
        """
        insert or replace into honest_eval_matchups
        (source_path, build_id, build_key, opponent_variant_id, replicate_idx, target)
        values (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.source_path,
                row.build_id,
                row.build_key,
                row.opponent_variant_id,
                row.replicate_idx,
                row.target,
            )
            for row in honest_eval_matchups
        ],
    )
    con.commit()
    con.close()


def load_recovered_builds(db_path: Path) -> tuple[RecoveredBuild, ...]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            """
            select build_key, source_kind, campaign, study, seed, rank, trial_number,
                   score, source_path, build_json
            from recovered_builds
            order by source_path, trial_number, rank, build_key
            """
        ).fetchall()
        return tuple(
            RecoveredBuild(
                build_key=str(build_key_value),
                build=_build_from_canonical(json.loads(build_json)),
                source_kind=BuildSourceKind(source_kind),
                campaign=campaign,
                study=study,
                seed=seed,
                rank=rank,
                trial_number=trial_number,
                score=score,
                source_path=str(source_path),
            )
            for (
                build_key_value,
                source_kind,
                campaign,
                study,
                seed,
                rank,
                trial_number,
                score,
                source_path,
                build_json,
            ) in rows
        )
    finally:
        con.close()


def load_training_matchups(db_path: Path) -> tuple[TrainingMatchupRow, ...]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            """
            select source_path, campaign, seed, trial_number, build_key,
                   opponent_variant_id, opponent_index, target, row_kind
            from training_matchups
            order by source_path, trial_number, opponent_index
            """
        ).fetchall()
        return tuple(TrainingMatchupRow(*row) for row in rows)
    finally:
        con.close()


def load_honest_eval_matchups(db_path: Path) -> tuple[HonestEvalMatchupRow, ...]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            """
            select source_path, build_id, build_key, opponent_variant_id,
                   replicate_idx, target
            from honest_eval_matchups
            order by source_path, build_id, opponent_variant_id, replicate_idx
            """
        ).fetchall()
        return tuple(HonestEvalMatchupRow(*row) for row in rows)
    finally:
        con.close()


def _validate_fraction(name: str, value: float) -> None:
    if not 0.0 < value < 1.0:
        raise ValueError(f"{name} must be in (0, 1), got {value}")


def _group_split(
    rows: Sequence[TrainingMatchupRow | HonestEvalMatchupRow],
    groups: Sequence[str],
    holdout_fraction: float,
    seed: int,
) -> SplitIds:
    _validate_fraction("holdout_fraction", holdout_fraction)
    unique = sorted(set(groups))
    if not unique:
        return SplitIds(train=(), test=())
    rng = random.Random(seed)
    rng.shuffle(unique)
    holdout_n = max(1, min(len(unique) - 1, round(len(unique) * holdout_fraction)))
    holdout = set(unique[:holdout_n])
    train: list[TrainingMatchupRow | HonestEvalMatchupRow] = []
    test: list[TrainingMatchupRow | HonestEvalMatchupRow] = []
    for row, group in zip(rows, groups, strict=True):
        (test if group in holdout else train).append(row)
    return SplitIds(train=tuple(train), test=tuple(test))


def held_out_build_split(
    rows: Sequence[TrainingMatchupRow], holdout_fraction: float, seed: int
) -> SplitIds:
    return _group_split(rows, [row.build_key for row in rows], holdout_fraction, seed)


def held_out_opponent_split(
    rows: Sequence[TrainingMatchupRow], holdout_fraction: float, seed: int
) -> SplitIds:
    return _group_split(
        rows, [row.opponent_variant_id for row in rows], holdout_fraction, seed
    )


def held_out_replicate_split(
    rows: Sequence[HonestEvalMatchupRow], holdout_fraction: float, seed: int
) -> SplitIds:
    return _group_split(
        rows,
        [
            f"{row.build_key or row.build_id}:{row.opponent_variant_id}"
            for row in rows
        ],
        holdout_fraction,
        seed,
    )


def held_out_component_combination_split(
    rows: Sequence[TrainingMatchupRow],
    build_lookup: Mapping[str, Build],
    holdout_fraction: float,
    seed: int,
) -> SplitIds:
    groups = []
    for row in rows:
        build = build_lookup[row.build_key]
        weapons = tuple(sorted(w for w in build.weapon_assignments.values() if w))
        hullmods = tuple(sorted(build.hullmods))
        groups.append(json.dumps({"w": weapons, "h": hullmods}, sort_keys=True))
    return _group_split(rows, groups, holdout_fraction, seed)


def held_out_seed_cell_split(
    rows: Sequence[TrainingMatchupRow], holdout_fraction: float, seed: int
) -> SplitIds:
    return _group_split(
        rows,
        [f"{row.campaign}:{row.seed}" for row in rows],
        holdout_fraction,
        seed,
    )


def forward_time_split(rows: Sequence[TrainingMatchupRow], train_fraction: float) -> SplitIds:
    _validate_fraction("train_fraction", train_fraction)
    ordered = sorted(rows, key=lambda row: (row.source_path, row.trial_number, row.opponent_index))
    if not ordered:
        return SplitIds(train=(), test=())
    split_idx = max(1, min(len(ordered) - 1, round(len(ordered) * train_fraction)))
    return SplitIds(train=tuple(ordered[:split_idx]), test=tuple(ordered[split_idx:]))
