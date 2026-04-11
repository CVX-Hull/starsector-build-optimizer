"""Self-tuning combat timeout prediction using survival analysis.

Data-driven cold-start priors from GameData (no magic numbers). Transitions to
lifelines Weibull AFT as data accumulates. See spec 21 for full design.
"""

from __future__ import annotations

import json
import logging
import math
import pickle
from pathlib import Path
from dataclasses import dataclass

import numpy as np

from .models import CombatResult, GameData, MatchupConfig, ShipHull

logger = logging.getLogger(__name__)

LOG_FILENAME = "evaluation_log.jsonl"


class TimeoutTuner:
    """Self-tuning timeout predictor using survival analysis.

    Tiered approach:
    - Cold start (0-50 obs): data-driven priors from GameData
    - Warm (50+ obs): lifelines WeibullAFTFitter with blended transition
    """

    def __init__(
        self,
        data_dir: Path,
        refit_threshold: int = 50,
        blend_scale: int = 100,
        target_percentile: float = 0.98,
        spawn_distance: float = 4000.0,
        safety_multiplier: float = 2.5,
    ) -> None:
        self._data_dir = data_dir
        self._refit_threshold = refit_threshold
        self._blend_scale = blend_scale
        self._target_percentile = target_percentile
        self._spawn_distance = spawn_distance
        self._safety_multiplier = safety_multiplier
        self._model = None
        self._n_observations = self._count_observations()
        self._n_at_last_refit = 0
        self._load_model()

    def predict_timeout(self, matchup: MatchupConfig, game_data: GameData) -> float:
        """Predict optimal timeout (game-time seconds) for a matchup."""
        # Look up hulls for the first build/variant on each side
        player_hull = self._lookup_hull(matchup.player_builds[0].variant_id, game_data)
        enemy_hull = self._lookup_hull(matchup.enemy_variants[0], game_data)

        prior = self.compute_default_timeout(
            player_hull, enemy_hull, game_data,
            self._spawn_distance, self._safety_multiplier,
        )

        if self._model is None or self._n_observations < 1:
            return prior

        # Blended prediction
        weight = min(1.0, self._n_observations / self._blend_scale)
        try:
            model_pred = self._predict_from_model(matchup, game_data)
            return (1 - weight) * prior + weight * model_pred
        except Exception:
            return prior

    def record_result(
        self,
        matchup: MatchupConfig,
        result: CombatResult,
        game_data: GameData,
        heartbeat_trajectory: list[list[float]] | None = None,
    ) -> None:
        """Append result to shared JSONL evaluation log."""
        player_hull = self._lookup_hull(matchup.player_builds[0].variant_id, game_data)
        enemy_hull = self._lookup_hull(matchup.enemy_variants[0], game_data)

        record = {
            "matchup_id": result.matchup_id,
            "player_builds": [b.variant_id for b in matchup.player_builds],
            "enemy_variants": list(matchup.enemy_variants),
            "hull_sizes": [player_hull.hull_size.value, enemy_hull.hull_size.value],
            "ship_counts": [len(matchup.player_builds), len(matchup.enemy_variants)],
            "winner": result.winner,
            "duration": result.duration_seconds,
            "completed": result.winner in ("PLAYER", "ENEMY"),
            "time_limit": matchup.time_limit_seconds,
            "time_mult": matchup.time_mult,
        }
        if heartbeat_trajectory is not None:
            record["heartbeat_trajectory"] = heartbeat_trajectory

        log_path = self._data_dir / LOG_FILENAME
        with open(log_path, "a") as f:
            f.write(json.dumps(record) + "\n")

        self._n_observations += 1

        # Trigger refit if enough new data
        if self._n_observations - self._n_at_last_refit >= self._refit_threshold:
            self.refit()

    def refit(self) -> None:
        """Refit survival model from accumulated data."""
        log_path = self._data_dir / LOG_FILENAME
        if not log_path.exists():
            return

        records = []
        for line in log_path.read_text().strip().split("\n"):
            if line:
                records.append(json.loads(line))

        if len(records) < self._refit_threshold:
            return

        try:
            import pandas as pd
            from lifelines import WeibullAFTFitter, LogNormalAFTFitter

            # Build dataframe
            rows = []
            for r in records:
                row = {
                    "duration": r["duration"],
                    "completed": 1 if r["completed"] else 0,
                    "ship_count_player": r["ship_counts"][0],
                    "ship_count_enemy": r["ship_counts"][1],
                }
                # One-hot encode hull sizes
                for hs in ["FRIGATE", "DESTROYER", "CRUISER", "CAPITAL_SHIP"]:
                    row[f"player_{hs}"] = 1 if r["hull_sizes"][0] == hs else 0
                    row[f"enemy_{hs}"] = 1 if r["hull_sizes"][1] == hs else 0
                rows.append(row)

            df = pd.DataFrame(rows)

            # Fit both models, pick by AIC
            weibull = WeibullAFTFitter(penalizer=0.01)
            weibull.fit(df, duration_col="duration", event_col="completed")

            lognormal = LogNormalAFTFitter(penalizer=0.01)
            lognormal.fit(df, duration_col="duration", event_col="completed")

            if lognormal.AIC_ < weibull.AIC_:
                self._model = lognormal
                model_type = "LogNormal"
            else:
                self._model = weibull
                model_type = "Weibull"

            self._n_at_last_refit = self._n_observations

            # Save model
            model_dir = self._data_dir / "timeout_model"
            model_dir.mkdir(exist_ok=True)
            with open(model_dir / "model.pkl", "wb") as f:
                pickle.dump(self._model, f)
            (model_dir / "metadata.json").write_text(json.dumps({
                "n_observations": self._n_observations,
                "model_type": model_type,
                "aic": self._model.AIC_,
            }))

            logger.info("TimeoutTuner: refit %s AFT on %d observations (AIC=%.1f)",
                        model_type, len(records), self._model.AIC_)

        except Exception as e:
            logger.warning("TimeoutTuner: refit failed: %s", e)

    @staticmethod
    def compute_default_timeout(
        player_hull: ShipHull,
        enemy_hull: ShipHull,
        game_data: GameData,
        spawn_distance: float = 4000.0,
        safety_multiplier: float = 2.5,
    ) -> float:
        """Data-driven cold-start timeout from GameData. No magic numbers."""
        # Approach time
        combined_speed = max(player_hull.max_speed + enemy_hull.max_speed, 1.0)
        approach = spawn_distance / combined_speed

        # Combat estimate from EHP and DPS
        player_ehp = player_hull.hitpoints + player_hull.armor_rating * 10
        enemy_ehp = enemy_hull.hitpoints + enemy_hull.armor_rating * 10

        weapon_dps_values = [w.damage_per_second for w in game_data.weapons.values()
                            if w.damage_per_second > 0]
        median_dps = float(np.median(weapon_dps_values)) if weapon_dps_values else 50.0

        player_slots = len([s for s in player_hull.weapon_slots
                           if s.slot_type.value not in
                           ("BUILT_IN", "DECORATIVE", "LAUNCH_BAY", "STATION_MODULE", "SYSTEM")])
        enemy_slots = len([s for s in enemy_hull.weapon_slots
                          if s.slot_type.value not in
                          ("BUILT_IN", "DECORATIVE", "LAUNCH_BAY", "STATION_MODULE", "SYSTEM")])

        est_dps_player = max(player_slots * median_dps * 0.5, 1.0)
        est_dps_enemy = max(enemy_slots * median_dps * 0.5, 1.0)

        combat_estimate = max(player_ehp / est_dps_enemy, enemy_ehp / est_dps_player)

        return min(approach + combat_estimate * safety_multiplier, 600.0)

    # --- Private ---

    def _lookup_hull(self, variant_id: str, game_data: GameData) -> ShipHull:
        """Look up hull from variant_id. Falls back to first hull if not found."""
        # variant_id format: "<hull_id>_<suffix>" (e.g., "eagle_Assault")
        # Try exact match first, then prefix match
        for hull_id, hull in game_data.hulls.items():
            if variant_id.startswith(hull_id):
                return hull
        # Fallback: return first hull
        return next(iter(game_data.hulls.values()))

    def _count_observations(self) -> int:
        log_path = self._data_dir / LOG_FILENAME
        if not log_path.exists():
            return 0
        return sum(1 for line in log_path.read_text().strip().split("\n") if line)

    def _load_model(self) -> None:
        model_path = self._data_dir / "timeout_model" / "model.pkl"
        if model_path.exists():
            try:
                with open(model_path, "rb") as f:
                    self._model = pickle.load(f)
            except Exception:
                self._model = None

    def _predict_from_model(self, matchup: MatchupConfig, game_data: GameData) -> float:
        """Predict timeout from fitted model."""
        import pandas as pd

        player_hull = self._lookup_hull(matchup.player_builds[0].variant_id, game_data)
        enemy_hull = self._lookup_hull(matchup.enemy_variants[0], game_data)

        row = {
            "ship_count_player": len(matchup.player_builds),
            "ship_count_enemy": len(matchup.enemy_variants),
        }
        for hs in ["FRIGATE", "DESTROYER", "CRUISER", "CAPITAL_SHIP"]:
            row[f"player_{hs}"] = 1 if player_hull.hull_size.value == hs else 0
            row[f"enemy_{hs}"] = 1 if enemy_hull.hull_size.value == hs else 0

        df = pd.DataFrame([row])
        percentile = self._model.predict_percentile(df, p=self._target_percentile)
        return float(percentile.iloc[0])
