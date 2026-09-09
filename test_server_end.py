import sys, time, json, os, traceback
from datetime import datetime, UTC
from skyguard.real_data_loader import fetch_open_meteo_data
from skyguard.pipeline import SkyGuardPipeline
from skyguard.correction import apply_corrections

PAST_DAYS = 10
REFRESH_INTERVAL_HOURS = 1
INJECT_ANOMALIES = False
STATIC_DIR = os.path.join(os.path.dirname(__file__), "output")
CACHE_FILE = os.path.join(STATIC_DIR, "_cache.json")

print("Fetching data...")
df = fetch_open_meteo_data(past_days=PAST_DAYS)
print("Data fetched. Running pipeline...")
results, health = SkyGuardPipeline(contamination=0.05).run(df, apply_correction=True)
print("Pipeline complete!")

metrics = {}
export_cols = [c for c in [
    "timestamp", "station_id",
    "temperature", "temperature_corrected",
    "pressure",    "pressure_corrected",
    "humidity",    "humidity_corrected",
    "anomaly_score", "confidence", "severity",
    "root_cause_label", "root_cause", "explanation",
    "is_flagged", "is_anomaly", "anomaly_type",
    "range_temperature", "range_pressure", "range_humidity",
    "rate_temperature",  "rate_pressure",  "rate_humidity",
    "frozen_temperature","frozen_pressure", "frozen_humidity",
    "zscore_temperature","zscore_pressure", "zscore_humidity",
    "multivariate_score", "ml_score", "spatial_score",
    "dew_point",
] if c in results.columns]

print("Creating dict...")
results_list = results[export_cols].assign(
    timestamp  = results["timestamp"].astype(str),
    is_flagged = results["is_flagged"].astype(bool),
).to_dict(orient="records")

now_utc  = datetime.now(UTC)
next_utc = datetime.fromtimestamp(
    time.time() + REFRESH_INTERVAL_HOURS * 3600, tz=UTC)

payload = {
    "status":             "ready",
    "last_run_utc":       now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "next_run_utc":       next_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "error":              None,
    "detection_results":  results_list,
    "sensor_health":      health.to_dict(orient="records"),
    "evaluation_metrics": metrics,
    "data_range": {
        "start":      str(results["timestamp"].min()),
        "end":        str(results["timestamp"].max()),
        "n_obs":      len(results),
        "n_stations": results["station_id"].nunique(),
    },
}

print("Saving cache...")
with open(CACHE_FILE, "w") as f:
    json.dump(payload, f)

print("Writing CSVs...")
results[export_cols].to_csv(
    os.path.join(STATIC_DIR, "detection_results.csv"), index=False)
health.to_csv(
    os.path.join(STATIC_DIR, "sensor_health_summary.csv"), index=False)
with open(os.path.join(STATIC_DIR, "evaluation_metrics.json"), "w") as f:
    json.dump(metrics, f, indent=2)

print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M} UTC] Done — "
      f"{len(results):,} obs, {int(results['is_flagged'].sum()):,} flagged. "
      f"Next run: {next_utc:%H:%M} UTC")
