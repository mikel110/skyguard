"""
detectors.py
------------
Deterministic, explainable-by-construction detectors. Each detector returns
a per-record score in [0, 1] plus a human-readable reason. These map
directly onto the "sensor faults, spikes, frozen values, communication
errors" and "multivariate consistency" objectives in the problem statement.

Design choice: rules first, ML second. Rules give instant, zero-training-cost
explainability and catch the majority of hard sensor faults (frozen, out of
physical range, comms garbage). The ML layer (ml_detector.py) then catches
the *subtle* multivariate patterns rules can't enumerate.
"""

import numpy as np
import pandas as pd

# ---- Physically plausible sensor ranges (tunable per deployment region) ----
PHYSICAL_RANGES = {
    "temperature": (-40.0, 60.0),   # deg C, IMD operational envelope
    "pressure": (870.0, 1085.0),    # hPa, sea-level-reduced range
    "humidity": (0.0, 100.0),       # %
}

# Max plausible rate of change per 5-minute step under extreme but real weather
MAX_RATE_OF_CHANGE = {
    "temperature": 4.0,   # deg C / 5 min
    "pressure": 3.0,      # hPa / 5 min
    "humidity": 20.0,     # % / 5 min
}

# Sensor resolution: successive identical readings beyond this run-length are
# statistically implausible for a live sensor and point to a "frozen" fault.
FROZEN_RUN_LENGTH = {
    "temperature": 8,   # 8 * 5min = 40 min unchanged -> suspicious
    "pressure": 8,
    "humidity": 8,
}

# Noise floor: the smallest step-to-step change that's meaningfully "real"
# sensor movement rather than measurement noise. Without this floor, a
# robust z-score on the first difference blows up during genuinely calm
# periods (when the natural diff variance is near zero) and false-flags
# ordinary small fluctuations as spikes.
# Calibrated against the mean step-to-step diff of the simulated network
# (temp ~0.30°C, pressure ~0.17 hPa, humidity ~1.7%). The floor should
# sit at or slightly above the typical step so only genuinely large jumps
# are flagged. Recalibrate against real AWS data before deployment.
DIFF_NOISE_FLOOR = {
    "temperature": 0.30,
    "pressure": 0.18,
    "humidity": 1.5,
}


def range_check(series, param):
    lo, hi = PHYSICAL_RANGES[param]
    out_of_range = (series < lo) | (series > hi)
    return out_of_range.astype(float)


def rate_of_change_check(series, param, window=12):
    """Robust (MAD-based) z-score of the first difference -> spike score.
    A noise floor is applied to the MAD so that ordinary calm-period
    fluctuations (where the natural diff variance is near zero) don't
    produce runaway z-scores."""
    diff = series.diff().abs()
    med = diff.rolling(window, min_periods=5).median()
    mad_raw = (diff - med).abs().rolling(window, min_periods=5).median()
    mad = np.maximum(mad_raw, DIFF_NOISE_FLOOR[param] * 0.5) + 1e-6
    robust_z = (diff - med) / (1.4826 * mad)
    hard_violation = diff > MAX_RATE_OF_CHANGE[param]
    # also require the absolute step itself to clear the noise floor --
    # a "large" z-score on a physically tiny step is not a real spike.
    meaningful = diff > DIFF_NOISE_FLOOR[param]
    score = np.clip(robust_z.fillna(0) / 6.0, 0, 1) * meaningful.astype(float)
    score[hard_violation] = 1.0
    return score.fillna(0.0)


def frozen_value_check(series, param):
    """Detects a sensor stuck at a constant value for longer than plausible."""
    same_as_prev = series.diff().abs() < 1e-9
    run_id = (~same_as_prev).cumsum()
    run_len = same_as_prev.groupby(run_id).cumcount() + 1
    threshold = FROZEN_RUN_LENGTH[param]
    score = np.clip((run_len - threshold) / threshold, 0, 1)
    score[~same_as_prev] = np.maximum(score[~same_as_prev] - score[~same_as_prev], 0)
    return score.fillna(0.0)


def rolling_zscore_check(series, param, window=288):
    """General statistical outlier vs recent local climatology (catches
    calibration drift and slow bias creep that spike/frozen checks miss).

    window defaults to 288 samples (~24h at 5-min cadence) so the rolling
    mean/std spans a *full* diurnal cycle -- comparing against a shorter
    window would mistake the normal day/night swing itself for a drift
    anomaly."""
    mean = series.rolling(window, min_periods=48).mean()
    std = series.rolling(window, min_periods=48).std() + 1e-6
    z = (series - mean).abs() / std
    return np.clip(z.fillna(0) / 5.0, 0, 1)


def dew_point_celsius(temp_c, rh_pct):
    """Magnus-Tetens approximation."""
    a, b = 17.62, 243.12
    rh = np.clip(rh_pct, 0.1, 100)
    gamma = (a * temp_c) / (b + temp_c) + np.log(rh / 100.0)
    return (b * gamma) / (a - gamma)


def multivariate_consistency_check(df):
    """Physical cross-checks between temperature, humidity and pressure that
    a single-parameter threshold check cannot see:
      1. Dew point must never exceed air temperature (thermodynamic law).
      2. Pressure and humidity should not both swing sharply in the same
         instant absent a genuine frontal passage (flagged as *suspicious*,
         not certain -- combined with spatial check downstream).
    """
    dp = dew_point_celsius(df["temperature"].values, df["humidity"].values)
    dew_violation = np.clip((dp - df["temperature"].values) / 5.0, 0, 1)

    pres_jump = df["pressure"].diff().abs()
    hum_jump = df["humidity"].diff().abs()
    joint_jump = np.clip((pres_jump / 5.0) * (hum_jump / 20.0), 0, 1).fillna(0)

    score = np.maximum(dew_violation, joint_jump.values)
    return pd.Series(score, index=df.index), dp


def spatial_consistency_check(network_df, timestamp_col="timestamp", baseline_window=288,
                              cluster_map=None, valid_clusters=None,
                              max_radius_km=150, min_cluster_size=3):
    """Cross-station check: at each timestamp, compare every station's
    *anomaly relative to its own recent baseline* against its peers' same
    relative anomaly *within the same spatial cluster*.

    Stations sit at very different altitudes/climates (e.g. Shimla vs
    Jaisalmer), so comparing raw values directly would flag normal climatic
    differences as "anomalies". Instead we:
      1. Cluster stations by geographic proximity (haversine distance).
      2. Detrend each station against its own rolling climatology (residual).
      3. Within each valid cluster (>= min_cluster_size), check whether one
         station's residual is an outlier among the cluster's residuals.

    Stations without enough nearby neighbors skip the spatial check entirely
    (score = 0) — this is correct behavior, not missing data. The 1-neighbor
    case (2-station cluster) is folded into "skip" because a 2-point MAD
    is statistically unstable.

    If cluster_map and valid_clusters are provided (pre-computed by the
    pipeline), they are reused. Otherwise, clusters are built from lat/lon
    columns in the DataFrame, or all stations are treated as one cluster
    (legacy fallback if no location data is available)."""
    from .geo import build_spatial_clusters

    result = network_df.sort_values(["station_id", timestamp_col]).copy()

    # ---- Build or reuse spatial clusters ----
    if cluster_map is None or valid_clusters is None:
        if "latitude" in result.columns and "longitude" in result.columns:
            station_meta = result.groupby("station_id")[["latitude", "longitude"]].first()
            coords = {sid: (row.latitude, row.longitude)
                      for sid, row in station_meta.iterrows()}
            cluster_map, valid_clusters, _ = build_spatial_clusters(
                coords, max_radius_km=max_radius_km,
                min_cluster_size=min_cluster_size)
        else:
            # No location data: legacy fallback — all stations in one cluster
            stations = result["station_id"].unique()
            cluster_map = {s: 0 for s in stations}
            valid_clusters = {0} if len(stations) >= min_cluster_size else set()

    result["_cluster_id"] = result["station_id"].map(cluster_map)
    result["_in_valid_cluster"] = result["_cluster_id"].isin(valid_clusters)

    # ---- Compute per-station residuals (detrend against own climatology) ----
    for param in ["temperature", "pressure", "humidity"]:
        baseline = (
            result.groupby("station_id")[param]
            .transform(lambda s: s.rolling(baseline_window, min_periods=24, center=True).median())
        )
        result[f"_resid_{param}"] = result[param] - baseline

    # ---- Spatial scoring within clusters ----
    # Base noise floor: prevents MAD from collapsing near zero.
    # Scaled by sqrt(5/cluster_size) so smaller clusters get a wider floor
    # (MAD from 3 points is ~30% noisier than from 5; this compensates).
    base_noise_floor = {"temperature": 0.5, "pressure": 0.4, "humidity": 2.5}

    # Pre-compute cluster sizes for the adaptive floor
    cluster_size_series = result.groupby("_cluster_id")["station_id"].transform("nunique")

    for param in ["temperature", "pressure", "humidity"]:
        rcol = f"_resid_{param}"
        # Group by (timestamp, cluster) — NOT network-wide
        grp = result.groupby([timestamp_col, "_cluster_id"])[rcol]
        med = grp.transform("median")
        mad_raw = grp.transform(lambda s: (s - s.median()).abs().median())

        # Adaptive noise floor
        size_factor = np.sqrt(5.0 / np.maximum(cluster_size_series, 1))
        floor = base_noise_floor[param] * size_factor
        mad = np.maximum(mad_raw, floor) + 1e-6

        robust_z = (result[rcol] - med).abs() / (1.4826 * mad)
        score = np.clip(robust_z / 6.0, 0, 1)

        n_in_cluster = grp.transform("count")
        # Zero out: not in a valid cluster, OR fewer than min_cluster_size
        # stations actually reporting at this timestamp in this cluster
        score = np.where(
            result["_in_valid_cluster"] & (n_in_cluster >= min_cluster_size),
            score, 0.0)
        result[f"spatial_score_{param}"] = score

    result["spatial_score"] = result[[c for c in result.columns
                                      if c.startswith("spatial_score_")]].max(axis=1)

    # Clean up temporary columns
    drop_cols = [c for c in result.columns
                 if c.startswith("_resid_") or c in ("_cluster_id", "_in_valid_cluster")]
    result = result.drop(columns=drop_cols)
    return result


def run_rule_based_detectors(df):
    """Applies all single-station rule detectors to one station's ordered
    time series and appends per-check score columns."""
    df = df.sort_values("timestamp").reset_index(drop=True)
    for param in ["temperature", "pressure", "humidity"]:
        df[f"range_{param}"] = range_check(df[param], param)
        df[f"rate_{param}"] = rate_of_change_check(df[param], param)
        df[f"frozen_{param}"] = frozen_value_check(df[param], param)
        df[f"zscore_{param}"] = rolling_zscore_check(df[param], param)

    mv_score, dew_point = multivariate_consistency_check(df)
    df["multivariate_score"] = mv_score
    df["dew_point"] = dew_point
    return df
