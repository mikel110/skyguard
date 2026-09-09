"""
pipeline.py
-----------
End-to-end orchestration: raw network data -> rule-based detectors ->
ML multivariate detector -> spatial consistency -> fusion -> correction ->
sensor health. Exposes both a batch `run()` for historical data and a
`RealTimeSession` class that processes one new reading at a time, which is
the shape a genuine streaming (MQTT/Kafka) deployment would take.

Spatial clustering is built once from station lat/lon and passed to both
the spatial consistency check and spatial correction — keeping the two
paths in sync so they always agree on which stations are "nearby."
"""

import numpy as np
import pandas as pd

from .detectors import run_rule_based_detectors, spatial_consistency_check
from .ml_detector import MultivariateAnomalyDetector, build_feature_matrix
from .fusion import fuse
from .correction import apply_corrections
from .health import compute_health
from .geo import build_spatial_clusters


def _extract_station_coords(df):
    """Extract station_id -> (lat, lon) mapping from the DataFrame.
    Returns None if lat/lon columns are not present."""
    if "latitude" not in df.columns or "longitude" not in df.columns:
        return None
    meta = df.groupby("station_id")[["latitude", "longitude"]].first()
    return {sid: (row.latitude, row.longitude) for sid, row in meta.iterrows()}


class SkyGuardPipeline:
    def __init__(self, contamination=0.05):
        self.contamination = contamination
        self.models_ = {}  # station_id -> fitted MultivariateAnomalyDetector
        self.cluster_map_ = None
        self.valid_clusters_ = None
        self.neighbor_map_ = None

    def _build_spatial_info(self, df):
        """Build spatial clusters once from lat/lon. Cached for reuse."""
        coords = _extract_station_coords(df)
        if coords is not None:
            self.cluster_map_, self.valid_clusters_, self.neighbor_map_ = \
                build_spatial_clusters(coords)
        else:
            self.cluster_map_ = None
            self.valid_clusters_ = None
            self.neighbor_map_ = None

    def _run_single_station_rules(self, df):
        # Note: pandas 3.x groupby.apply drops the grouping column from the
        # sub-frame it passes in, so we iterate explicitly and reattach it.
        frames = []
        for station_id, g in df.groupby("station_id"):
            processed = run_rule_based_detectors(g)
            processed["station_id"] = station_id
            frames.append(processed)
        return pd.concat(frames, ignore_index=True)

    def _run_ml_layer(self, df, refit=True):
        scored_frames = []
        for station_id, g in df.groupby("station_id"):
            g = g.sort_values("timestamp").reset_index(drop=True)
            if refit or station_id not in self.models_:
                model = MultivariateAnomalyDetector(contamination=self.contamination)
                model.fit(g)
                self.models_[station_id] = model
            else:
                model = self.models_[station_id]
            scores, X = model.score(g)
            g["ml_score"] = scores.values
            g["_feature_matrix_idx"] = list(X.index)
            scored_frames.append(g)
        return pd.concat(scored_frames, ignore_index=True)

    def run(self, network_df, apply_correction=True, refit_ml=True):
        """Full batch pipeline over a historical/simulated multi-station
        dataset. Returns (results_df, health_summary_df).

        `refit_ml=False` reuses previously fitted per-station ML models
        instead of retraining -- used by RealTimeSession so a live stream
        doesn't retrain a fresh IsolationForest on every single tick (in
        production, retraining would instead be scheduled periodically,
        e.g. hourly, on a background thread)."""
        df = network_df.copy().sort_values(["station_id", "timestamp"]).reset_index(drop=True)

        # Build spatial clusters once from lat/lon
        self._build_spatial_info(df)

        df = self._run_single_station_rules(df)
        df = self._run_ml_layer(df, refit=refit_ml)
        df = spatial_consistency_check(
            df, cluster_map=self.cluster_map_,
            valid_clusters=self.valid_clusters_)
        df = fuse(df)

        if apply_correction:
            df = apply_corrections(df, neighbor_map=self.neighbor_map_)

        df, health_summary = compute_health(df)
        return df, health_summary

    def explain_record(self, results_df, record_index):
        """Rich, human + machine readable explanation for one flagged record,
        combining the rule-check breakdown with ML feature attribution."""
        row = results_df.loc[record_index]
        station_id = row["station_id"]
        model = self.models_.get(station_id)

        out = {
            "timestamp": str(row["timestamp"]),
            "station_id": station_id,
            "anomaly_score": round(float(row["anomaly_score"]), 3),
            "confidence": round(float(row["confidence"]), 3),
            "severity": row["severity"],
            "root_cause": row["root_cause_label"],
            "rule_based_reasoning": row["explanation"],
            "raw_values": {
                "temperature": round(float(row["temperature"]), 2),
                "pressure": round(float(row["pressure"]), 2),
                "humidity": round(float(row["humidity"]), 2),
            },
            "corrected_values": {
                "temperature": round(float(row.get("temperature_corrected", row["temperature"])), 2),
                "pressure": round(float(row.get("pressure_corrected", row["pressure"])), 2),
                "humidity": round(float(row.get("humidity_corrected", row["humidity"])), 2),
            },
        }

        if model is not None and model.fitted:
            station_df = results_df[results_df["station_id"] == station_id].reset_index(drop=True)
            match = station_df[station_df["timestamp"] == row["timestamp"]]
            if len(match):
                X = build_feature_matrix(station_df)
                local_idx = match.index[0]
                out["ml_feature_attribution"] = model.explain_point(X.loc[local_idx])
        return out


class RealTimeSession:
    """Simulates a streaming deployment: a warm-up batch trains the models,
    then `ingest()` scores one new reading at a time against a rolling
    buffer, mirroring how this would run against a live MQTT/REST feed from
    an AWS data logger (with periodic model refresh in the background)."""

    def __init__(self, warmup_df, buffer_size=576, contamination=0.05):
        self.pipeline = SkyGuardPipeline(contamination=contamination)
        self.buffers = {
            sid: g.sort_values("timestamp").tail(buffer_size).reset_index(drop=True)
            for sid, g in warmup_df.groupby("station_id")
        }
        self.buffer_size = buffer_size
        self._tick_count = 0
        self.refit_every = 100  # periodic background-style retrain cadence
        _, _ = self.pipeline.run(warmup_df, apply_correction=False, refit_ml=True)  # warm-start models

    def ingest(self, network_snapshot_df):
        """network_snapshot_df: one row per station for a single new
        timestamp. Returns the fused/explained results for just this tick."""
        for sid, g in network_snapshot_df.groupby("station_id"):
            self.buffers[sid] = pd.concat([self.buffers[sid], g], ignore_index=True).tail(self.buffer_size)

        self._tick_count += 1
        refit = (self._tick_count % self.refit_every == 0)

        combined = pd.concat(self.buffers.values(), ignore_index=True)
        results, health = self.pipeline.run(combined, apply_correction=True, refit_ml=refit)
        latest_ts = network_snapshot_df["timestamp"].iloc[0]
        return results[results["timestamp"] == latest_ts].reset_index(drop=True), health
