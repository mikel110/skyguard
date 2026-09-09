"""
health.py
---------
Tracks per-station, per-parameter sensor health over time using an
exponentially-weighted anomaly rate, and turns that into:
  - a health status label (Healthy / Watch / Degraded / Faulty)
  - a rough "predicted days to maintenance" estimate, extrapolating the
    trend in anomaly rate forward to a failure threshold.

This directly targets the "predict possible sensor degradation and
maintenance requirements" objective without requiring failure-labeled
historical data (which most AWS networks don't have) -- it's a trend-based
heuristic that can later be replaced by a survival-analysis / RUL model
once real failure logs are available.
"""

import numpy as np
import pandas as pd

HEALTH_BANDS = [
    (0.35, "Faulty - immediate maintenance recommended"),
    (0.18, "Degraded - schedule maintenance soon"),
    (0.06, "Watch - elevated anomaly rate, monitor"),
]

EWMA_HALF_LIFE = 96  # ~8 hours at 5-min cadence


def compute_health(df, group_cols=("station_id",)):
    """Adds `ewma_anomaly_rate` and returns a per-station health summary
    DataFrame with status and a naive linear-trend maintenance estimate."""
    df = df.sort_values("timestamp").copy()
    df["ewma_anomaly_rate"] = (
        df.groupby(list(group_cols))["is_flagged"]
        .transform(lambda s: s.astype(float).ewm(halflife=EWMA_HALF_LIFE).mean())
    )

    summaries = []
    for keys, g in df.groupby(list(group_cols)):
        station_id = keys if isinstance(keys, str) else keys[0]
        current_rate = g["ewma_anomaly_rate"].iloc[-1]
        status = "Healthy"
        for threshold, label in HEALTH_BANDS:
            if current_rate >= threshold:
                status = label
                break

        # crude linear trend on the last ~48h of ewma rate to project forward
        recent = g["ewma_anomaly_rate"].tail(576).reset_index(drop=True)  # ~48h @5min
        days_to_maintenance = None
        if len(recent) > 10:
            x = np.arange(len(recent))
            slope, intercept = np.polyfit(x, recent.values, 1)
            if slope > 1e-6:
                # steps until crossing the "Faulty" threshold (0.35)
                target = HEALTH_BANDS[0][0]
                steps_needed = (target - current_rate) / slope
                minutes_per_step = (g["timestamp"].diff().median().total_seconds() / 60.0
                                    if g["timestamp"].diff().notna().any() else 5)
                days_to_maintenance = max(round(steps_needed * minutes_per_step / 1440, 1), 0)

        summaries.append({
            "station_id": station_id,
            "current_anomaly_rate": round(float(current_rate), 4),
            "health_status": status,
            "estimated_days_to_maintenance": days_to_maintenance,
            "total_flagged_observations": int(g["is_flagged"].sum()),
            "total_observations": int(len(g)),
        })

    return df, pd.DataFrame(summaries).sort_values("current_anomaly_rate", ascending=False)
