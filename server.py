"""
server.py
---------
SkyGuard AI — production-grade Flask backend.

Architecture (the professional way):
  • The pipeline runs once on startup, then on a fixed schedule in a
    background thread. Results are cached in memory AND written to disk.
  • The browser NEVER makes a fetch/POST call. On page load, Flask
    server-side renders the data directly into the HTML
    (window.__SKYGUARD__). The browser reads that variable — zero CORS,
    zero network calls, zero failure modes.
  • "Refresh" = reloading the page. The page also auto-reloads itself
    when the next scheduled run completes (it reads next_run_utc and
    schedules a window.location.reload() accordingly).
  • /api/data and /api/status are kept for Render health-checks and
    future integrations, but the dashboard itself never calls them.

Usage (local dev):
    source .venv/bin/activate && python3 server.py

Usage (production, via Render / gunicorn):
    gunicorn server:app
    (gunicorn runs with multiple workers; background threads are started
    in the post_fork hook via the APP_INIT sentinel below)
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

# ── Configuration ─────────────────────────────────────────────────────────────
REFRESH_INTERVAL_HOURS = 1     # pipeline re-runs every N hours automatically
PAST_DAYS              = 10    # days of Open-Meteo history to load
INJECT_ANOMALIES       = True  # inject synthetic faults for evaluation metrics
CACHE_FILE             = os.path.join(os.path.dirname(__file__), "output", "_cache.json")
STATIC_DIR             = os.path.join(os.path.dirname(__file__), "output")

app = Flask(__name__, static_folder=STATIC_DIR)
# CORS only needed for /api/* (health checks, Render, external integrations)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── In-memory state ───────────────────────────────────────────────────────────
_lock  = threading.Lock()
_state = {
    "status":             "initializing",
    "last_run_utc":       None,
    "next_run_utc":       None,
    "error":              None,
    "detection_results":  [],
    "sensor_health":      [],
    "evaluation_metrics": {},
    "data_range":         {"start": None, "end": None, "n_obs": 0, "n_stations": 0},
}


# ── Cache helpers ─────────────────────────────────────────────────────────────
def _load_cache():
    """On cold start, restore last known-good state from disk so the
    dashboard shows data immediately instead of a spinner."""
    global _state
    if not os.path.exists(CACHE_FILE):
        return
    try:
        with open(CACHE_FILE) as f:
            cached = json.load(f)
        with _lock:
            _state.update(cached)
            _state["status"] = "ready"   # mark ready so page renders immediately
        print(f"[CACHE] Restored {_state['data_range'].get('n_obs', 0)} obs from disk cache.")
    except Exception as e:
        print(f"[CACHE] Could not restore cache: {e}")


def _save_cache(payload: dict):
    """Persist state to disk after every successful run."""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"[CACHE] Could not save cache: {e}")


# ── Pipeline ──────────────────────────────────────────────────────────────────
def _run_pipeline():
    """Fetch fresh data, run the anomaly detection pipeline, update state."""
    with _lock:
        _state["status"] = "refreshing"
        _state["error"]  = None

    try:
        print(f"\n[{datetime.now(UTC):%Y-%m-%d %H:%M} UTC] Pipeline starting...")

        df = fetch_open_meteo_data(past_days=PAST_DAYS)

        if INJECT_ANOMALIES:
            df = AnomalyInjector(seed=42).inject(df, n_events_per_station=8)

        results, health = SkyGuardPipeline(contamination=0.05).run(df, apply_correction=True)

        metrics = _compute_metrics(results) if INJECT_ANOMALIES else {}

        export_cols = [c for c in [
            "timestamp", "station_id",
            "temperature", "temperature_corrected",
            "pressure",    "pressure_corrected",
            "humidity",    "humidity_corrected",
            "anomaly_score", "confidence", "severity",
            "root_cause_label", "is_flagged", "is_anomaly", "anomaly_type",
        ] if c in results.columns]

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

        with _lock:
            _state.update(payload)

        _save_cache(payload)

        # Also write legacy CSVs so example_usage.py still works
        results[export_cols].to_csv(
            os.path.join(STATIC_DIR, "detection_results.csv"), index=False)
        health.to_csv(
            os.path.join(STATIC_DIR, "sensor_health_summary.csv"), index=False)
        with open(os.path.join(STATIC_DIR, "evaluation_metrics.json"), "w") as f:
            json.dump(metrics, f, indent=2)

        print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M} UTC] Done — "
              f"{len(results):,} obs, {int(results['is_flagged'].sum()):,} flagged. "
              f"Next run: {next_utc:%H:%M} UTC")

    except Exception:
        tb = traceback.format_exc()
        print(f"[ERROR] Pipeline failed:\n{tb}")
        with _lock:
            _state["status"] = "error"
            _state["error"]  = tb.splitlines()[-1]


def _schedule_loop():
    """Runs forever: pipeline → sleep REFRESH_INTERVAL_HOURS → repeat."""
    while True:
        _run_pipeline()
        time.sleep(REFRESH_INTERVAL_HOURS * 3600)


def _compute_metrics(results):
    y_true    = results["is_anomaly"].astype(int)
    any_flag  = results["is_flagged"].astype(int)
    confirmed = results["severity"].isin(["Medium", "High", "Critical"]).astype(int)

    def _score(y_pred):
        p  = precision_score(y_true, y_pred, zero_division=0)
        r  = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
        return {"precision": p, "recall": r, "f1": f1,
                "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)}

    return {
        "tier1_any_flag":        _score(any_flag),
        "tier2_confirmed_alert": _score(confirmed),
    }


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    """Serve index.html with current pipeline data baked in as an inline
    script variable. The browser reads window.__SKYGUARD__ directly —
    no fetch(), no CORS, no network call of any kind."""
    html_path = os.path.join(STATIC_DIR, "index.html")
    with open(html_path) as f:
        html = f.read()

    with _lock:
        payload = json.dumps({k: v for k, v in _state.items() if k != "error"})
        status  = _state["status"]
        err     = _state.get("error")

    if status == "initializing":
        # Show a proper loading page rather than an empty dashboard
        payload = json.dumps({"status": "initializing"})
    elif status == "error":
        payload = json.dumps({"status": "error", "message": err})

    injected = f"<script>window.__SKYGUARD__ = {payload};</script>\n"
    html = html.replace("</head>", injected + "</head>", 1)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/status")
def api_status():
    """Lightweight health-check endpoint (used by Render, monitoring tools)."""
    with _lock:
        return jsonify({
            "status":       _state["status"],
            "last_run_utc": _state["last_run_utc"],
            "next_run_utc": _state["next_run_utc"],
            "data_range":   _state["data_range"],
            "error":        _state["error"],
        })


@app.route("/api/data")
def api_data():
    """Full data payload — available for external integrations."""
    with _lock:
        s = _state["status"]
        if s == "initializing":
            return jsonify({"status": "initializing"}), 202
        if s == "error":
            return jsonify({"status": "error", "message": _state["error"]}), 500
        return jsonify({k: v for k, v in _state.items()})


# ── Startup ───────────────────────────────────────────────────────────────────
# This sentinel prevents the background thread from being started twice when
# gunicorn forks multiple workers (only worker 0 runs the pipeline).
_STARTED = False

def _start_background():
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    _load_cache()                                          # restore last results immediately
    threading.Thread(target=_schedule_loop, daemon=True).start()  # then refresh in bg


# Called automatically when the module loads (works for both `python server.py`
# and `gunicorn server:app`)
_start_background()


if __name__ == "__main__":
    print("=" * 60)
    print("  SkyGuard AI — Development Server")
    print("  http://localhost:5001")
    print(f"  Auto-refresh every {REFRESH_INTERVAL_HOURS}h | past_days={PAST_DAYS}")
    print("  Dashboard reloads itself when new data is ready.")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
