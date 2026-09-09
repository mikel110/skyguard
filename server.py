"""
server.py
---------
Flask live backend for SkyGuard AI.

  • Serves the frontend (output/) as static files on /
  • Exposes GET /api/data  — returns fresh JSON from the pipeline
  • Exposes GET /api/status — returns pipeline run state
  • Refreshes the pipeline in a background thread every REFRESH_INTERVAL_HOURS

Usage:
    source .venv/bin/activate
    python3 server.py

Then open http://localhost:5001 in your browser.
"""

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone

UTC = timezone.utc

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from skyguard.real_data_loader import fetch_open_meteo_data
from skyguard.simulator import AnomalyInjector
from skyguard.pipeline import SkyGuardPipeline
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix

# ── Configuration ────────────────────────────────────────────────────────────
REFRESH_INTERVAL_HOURS = 1        # how often to re-fetch + re-run the pipeline
PAST_DAYS = 10                    # days of Open-Meteo history to fetch
INJECT_ANOMALIES = True           # keep True so evaluation metrics stay meaningful
STATIC_DIR = os.path.join(os.path.dirname(__file__), "output")

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app)  # allow the browser to call /api/* from any origin

# ── Shared state (written by background thread, read by request handlers) ────
_state_lock = threading.Lock()
_state = {
    "status": "initializing",    # "initializing" | "ready" | "refreshing" | "error"
    "last_run_utc": None,
    "next_run_utc": None,
    "error": None,
    "detection_results": [],
    "sensor_health": [],
    "evaluation_metrics": {},
    "data_range": {"start": None, "end": None, "n_obs": 0, "n_stations": 0},
}


# ── Pipeline runner ──────────────────────────────────────────────────────────
def _run_pipeline():
    """Fetch fresh data, run the full pipeline, update _state."""
    global _state

    with _state_lock:
        _state["status"] = "refreshing"
        _state["error"] = None

    try:
        print(f"\n[{datetime.now(UTC):%Y-%m-%d %H:%M} UTC] Starting pipeline refresh...")

        # 1. Fetch real data (future rows already filtered inside fetch)
        df = fetch_open_meteo_data(past_days=PAST_DAYS)

        # 2. Inject synthetic faults so evaluation metrics are meaningful
        if INJECT_ANOMALIES:
            injector = AnomalyInjector(seed=42)
            df = injector.inject(df, n_events_per_station=8)

        # 3. Run the detection pipeline
        pipeline = SkyGuardPipeline(contamination=0.05)
        results, health = pipeline.run(df, apply_correction=True)

        # 4. Compute evaluation metrics
        metrics = {}
        if INJECT_ANOMALIES and "is_anomaly" in results.columns:
            metrics = _compute_metrics(results)

        # 5. Serialise for JSON transport
        export_cols = [
            "timestamp", "station_id",
            "temperature", "temperature_corrected",
            "pressure",    "pressure_corrected",
            "humidity",    "humidity_corrected",
            "anomaly_score", "confidence", "severity",
            "root_cause_label", "is_flagged", "is_anomaly", "anomaly_type",
        ]
        # Keep only cols that actually exist (anomaly_type may be absent on real-only run)
        export_cols = [c for c in export_cols if c in results.columns]
        results_list = results[export_cols].assign(
            timestamp=results["timestamp"].astype(str),
            is_flagged=results["is_flagged"].astype(bool),
            is_anomaly=results.get("is_anomaly", False),
        ).to_dict(orient="records")

        health_list = health.to_dict(orient="records")

        now_utc  = datetime.now(UTC)
        next_utc = datetime.fromtimestamp(time.time() + REFRESH_INTERVAL_HOURS * 3600, tz=UTC)

        with _state_lock:
            _state["status"]             = "ready"
            _state["last_run_utc"]       = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            _state["next_run_utc"]       = next_utc.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa
            _state["detection_results"]  = results_list
            _state["sensor_health"]      = health_list
            _state["evaluation_metrics"] = metrics
            _state["data_range"] = {
                "start":      str(results["timestamp"].min()),
                "end":        str(results["timestamp"].max()),
                "n_obs":      len(results),
                "n_stations": results["station_id"].nunique(),
            }

        # Also write CSVs so example_usage.py / legacy tools still work
        out_dir = STATIC_DIR
        results[export_cols].to_csv(os.path.join(out_dir, "detection_results.csv"), index=False)
        health.to_csv(os.path.join(out_dir, "sensor_health_summary.csv"), index=False)
        with open(os.path.join(out_dir, "evaluation_metrics.json"), "w") as f:
            json.dump(metrics, f, indent=2)

        print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M} UTC] Pipeline refresh complete. "
              f"{len(results):,} observations, {int(results['is_flagged'].sum()):,} flagged.")

    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[ERROR] Pipeline refresh failed:\n{tb}")
        with _state_lock:
            _state["status"] = "error"
            _state["error"]  = str(exc)


def _compute_metrics(results):
    """Compute Tier 1 / Tier 2 evaluation metrics."""
    y_true = results["is_anomaly"].astype(int)

    def _score(y_pred, label):
        p  = precision_score(y_true, y_pred, zero_division=0)
        r  = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
        return {"precision": p, "recall": r, "f1": f1,
                "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)}

    any_flag  = results["is_flagged"].astype(int)
    confirmed = results["severity"].isin(["Medium", "High", "Critical"]).astype(int)
    return {
        "tier1_any_flag":         _score(any_flag,  "Tier1"),
        "tier2_confirmed_alert":  _score(confirmed, "Tier2"),
    }


# ── Background refresh loop ───────────────────────────────────────────────────
def _refresh_loop():
    while True:
        _run_pipeline()
        time.sleep(REFRESH_INTERVAL_HOURS * 3600)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    """Serve index.html with the current pipeline data injected as an inline
    script — this eliminates any fetch/CORS dependency entirely."""
    html_path = os.path.join(STATIC_DIR, "index.html")
    with open(html_path, "r") as f:
        html = f.read()

    with _state_lock:
        payload = json.dumps({
            "status":             _state["status"],
            "last_run_utc":       _state["last_run_utc"],
            "next_run_utc":       _state["next_run_utc"],
            "data_range":         _state["data_range"],
            "detection_results":  _state["detection_results"],
            "sensor_health":      _state["sensor_health"],
            "evaluation_metrics": _state["evaluation_metrics"],
        })

    # Inject before </head> so it's available before app.js runs
    injected = f'<script>window.__SKYGUARD__ = {payload};</script>\n'
    html = html.replace("</head>", injected + "</head>", 1)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/status")
def api_status():
    with _state_lock:
        return jsonify({
            "status":       _state["status"],
            "last_run_utc": _state["last_run_utc"],
            "next_run_utc": _state["next_run_utc"],
            "data_range":   _state["data_range"],
            "error":        _state["error"],
        })


@app.route("/api/data")
def api_data():
    with _state_lock:
        if _state["status"] == "initializing":
            return jsonify({"status": "initializing", "message": "Pipeline is running for the first time. Please wait ~60s."}), 202
        if _state["status"] == "error":
            return jsonify({"status": "error", "message": _state["error"]}), 500
        return jsonify({
            "status":             _state["status"],
            "last_run_utc":       _state["last_run_utc"],
            "next_run_utc":       _state["next_run_utc"],
            "data_range":         _state["data_range"],
            "detection_results":  _state["detection_results"],
            "sensor_health":      _state["sensor_health"],
            "evaluation_metrics": _state["evaluation_metrics"],
        })


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Kick off the pipeline immediately in a daemon thread
    t = threading.Thread(target=_refresh_loop, daemon=True)
    t.start()

    print("=" * 60)
    print("  SkyGuard AI — Live Backend")
    print("  http://localhost:5001")
    print(f"  Data refresh every {REFRESH_INTERVAL_HOURS}h  |  past_days={PAST_DAYS}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
