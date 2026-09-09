# SkyGuard AI — User Guide

> **Real-Time Anomaly Detection for Automatic Weather Stations**  
> Powered by Open-Meteo API · Isolation Forest · 7-Layer Rule Engine

---

## Table of Contents
1. [What Was Changed (Gemini Integration)](#what-was-changed)
2. [Quick Start](#quick-start)
3. [How the Data is Sourced](#how-the-data-is-sourced)
4. [Running the Pipeline](#running-the-pipeline)
5. [Viewing the Dashboard](#viewing-the-dashboard)
6. [Dashboard Walkthrough](#dashboard-walkthrough)
7. [Understanding the AI](#understanding-the-ai)
8. [Project Structure](#project-structure)
9. [Deployment Notes](#deployment-notes)

---

## What Was Changed

The previous session replaced the mathematical simulator with a **real-world data fetcher**. Here is a summary of what changed:

| File | Change |
|---|---|
| `skyguard/real_data_loader.py` | **NEW** — Fetches real hourly weather from Open-Meteo API for all 7 stations |
| `example_usage.py` | **MODIFIED** — Now calls `fetch_open_meteo_data()` instead of the simulator |
| `output/index.html` | **NEW** — Premium 3-tab interactive dashboard |
| `output/style.css` | **NEW** — Dark glassmorphism theme |
| `output/app.js` | **NEW** — Reads real CSVs and renders Plotly charts |
| `output/detection_results.csv` | **REGENERATED** — Now contains real Open-Meteo weather data |
| `output/sensor_health_summary.csv` | **REGENERATED** — Based on real data |

**What was NOT changed:** The core detection engine (`detectors.py`, `ml_detector.py`, `pipeline.py`, `geo.py`, `fusion.py`, `correction.py`, `health.py`) is entirely untouched.

---

## Quick Start

### Prerequisites
```bash
cd skyguard_ai
source .venv/bin/activate   # activate the existing virtual environment
pip install requests         # only new dependency added
```

### Run the pipeline
```bash
python3 example_usage.py
```

This will:
1. Fetch the last **10 days of real hourly weather** from Open-Meteo API for all 7 stations
2. Inject synthetic anomalies (spikes, frozen sensors, drift) so the AI has labeled ground truth to evaluate against
3. Run the full 7-layer detection pipeline
4. Write results to `output/`

### View the dashboard
```bash
cd output
python3 -m http.server 8081
```
Then open **http://localhost:8081** in your browser.

---

## How the Data is Sourced

Data now comes from the **[Open-Meteo API](https://open-meteo.com/)** — a free, no-API-key-required service that aggregates verified meteorological data from national weather services including NOAA, DWD, and MeteoFrance.

### The 7 Stations

| Station ID | Location | Cluster |
|---|---|---|
| `AWS_DELHI` | Delhi (28.61°N, 77.20°E) | NCR Cluster |
| `AWS_GURGAON` | Gurgaon (28.46°N, 77.03°E) | NCR Cluster |
| `AWS_NOIDA` | Noida (28.53°N, 77.39°E) | NCR Cluster |
| `AWS_KOCHI` | Kochi (9.93°N, 76.26°E) | Isolated |
| `AWS_BLR` | Bangalore (12.97°N, 77.59°E) | Isolated |
| `AWS_SHIMLA` | Shimla (31.10°N, 77.17°E) | Isolated |
| `AWS_JAISALMER` | Jaisalmer (26.91°N, 70.90°E) | Isolated |

### What is fetched
- `temperature_2m` — 2m air temperature (°C)
- `relative_humidity_2m` — Relative humidity (%)
- `surface_pressure` — Surface pressure (hPa)

### Why inject anomalies?
The Open-Meteo data is *clean* real weather — sensors at Open-Meteo don't break. We intentionally inject synthetic faults into the real data so we can:
1. Measure the AI's detection accuracy against a known ground truth
2. Demonstrate the dashboard catching real-looking faults during your presentation

> **Note:** To use **only** real data without injecting faults, remove lines 51–53 from `example_usage.py` (the `AnomalyInjector` block). The pipeline will still run, but the evaluation metrics will be meaningless since there are no labeled faults.

---

## Running the Pipeline

```bash
python3 example_usage.py
```

### What each step does

| Step | Description |
|---|---|
| **STEP 1** | Fetches 10 days of real weather data from Open-Meteo, then injects 8 synthetic fault events per station |
| **STEP 2** | Runs the full SkyGuard 7-layer detection pipeline |
| **STEP 3** | Evaluates Tier 1 (any flag) and Tier 2 (Medium+ severity) performance against ground truth |
| **STEP 4** | Prints a worked explainability example for a multivariate anomaly |
| **STEP 5** | Simulates real-time streaming tick-by-tick over the last 30% of data |
| **STEP 6** | Writes all outputs to `output/` |

### Output Files

| File | Description |
|---|---|
| `output/detection_results.csv` | Full record with anomaly scores, severity, root cause, corrected values |
| `output/sensor_health_summary.csv` | Per-station health status and maintenance predictions |
| `output/evaluation_metrics.json` | Precision, recall, F1, FAR for Tier 1 and Tier 2 |

---

## Viewing the Dashboard

### Start the server

```bash
cd output
python3 -m http.server 8081
```

Then navigate to:

```
http://localhost:8081
```

> **Important:** The dashboard reads the CSV files via `fetch()`. Opening `index.html` directly as `file://` will **not** work due to browser CORS restrictions — you must use the HTTP server.

---

## Dashboard Walkthrough

### Tab 1 — Network Overview

The control-room view for the entire 7-station network.

- **KPI Cards** — Total observations, anomaly count, Tier 2 precision, false alarm rate (sourced from `evaluation_metrics.json`)
- **Root Cause Pie** — Shows how faults are distributed across categories (spike, frozen, drift, spatial outlier, etc.)
- **Sensor Health Bar** — Ranks all 7 stations by anomaly rate; red = needs immediate maintenance
- **Live Alert Feed** — Scrollable chronological list of all flagged observations sorted newest-first, with severity badges

### Tab 2 — Station Deep-Dive

Select any station from the left sidebar to investigate it.

- **Sidebar** — Stations are grouped into "Delhi NCR Cluster" and "Isolated Stations" to reflect the spatial clustering architecture
- **Temperature Chart** — Blue = raw reading, Green dashed = AI-corrected value, Red × = detected anomaly
- **Pressure / Humidity** — Same pattern, separate panels
- **Fused Anomaly Score** — The final 0–1 output of the Evidence Fusion layer with Medium (0.4) and High (0.65) threshold lines shown

### Tab 3 — AI Explainability

- **7-Layer Pipeline Flowchart** — Visual walkthrough of every detection layer from raw reading to alert
- **Occlusion Feature Attribution** — Bar chart showing which features caused the highest-scoring anomaly
- **Worst Anomaly Breakdown** — Detailed card showing raw vs. corrected values for the most severe alert
- **Performance Metrics Grid** — Precision, Recall, F1, and False Alarm Rate for both tiers

---

## Understanding the AI

### The 7-Layer Pipeline

```
Raw AWS Reading
      ↓
Layer 1: Physical Range Check   (-40°C to 60°C | 870–1085 hPa | 0–100%)
      ↓
Layer 2: Spike Detector         (rate of change > physical maximum)
      ↓
Layer 3: Frozen Sensor          (value unchanged for 40+ minutes)
      ↓
Layer 4: Calibration Drift      (sustained deviation from rolling climatology)
      ↓
Layer 5: Isolation Forest       (multivariate anomaly on 7 engineered features)
      ↓
Layer 6: Spatial Consistency    (disagrees with Delhi/Gurgaon/Noida cluster)
      ↓
Layer 7: Evidence Fusion        (weighted scores → confidence → severity)
      ↓
Auto-Correction + Alert
```

### Two-Tier Severity

| Tier | Severity | Use |
|---|---|---|
| Tier 1 | Low+ (any flag) | High-recall QC screen — catch everything, review later |
| Tier 2 | Medium+ (confirmed) | Operational alert — low false alarm rate, pages the engineer |

### AI Explainability — Occlusion Attribution

The Isolation Forest gives a score but no reason. SkyGuard explains it using **occlusion-based feature attribution** (no SHAP library required):

1. Take the anomaly score of the flagged point
2. For each feature (temp, pressure, humidity, their rates of change, dew-point gap), **reset it to its local rolling median** and re-score
3. The feature whose removal causes the **largest drop in anomaly score** is the primary cause

This is mathematically equivalent to SHAP's leave-one-out approximation, but needs no extra dependency — making it edge-deployable on a Raspberry Pi at the weather station.

### Auto-Correction

When a fault is detected, SkyGuard does not just flag it — it computes a corrected value so downstream weather forecasting models still receive clean data:
- **Isolated stations**: Rolling-median imputation
- **NCR Cluster stations**: Spatial interpolation from neighboring stations (Gurgaon, Noida, Delhi)

---

## Project Structure

```
skyguard_ai/
├── example_usage.py          # Main entry point — run this
├── USER_GUIDE.md             # This file
├── requirements.txt
├── .venv/                    # Python virtual environment
├── skyguard/
│   ├── real_data_loader.py   # NEW: Open-Meteo API fetcher
│   ├── simulator.py          # Math simulator + AnomalyInjector (still used for fault injection)
│   ├── pipeline.py           # End-to-end orchestrator
│   ├── detectors.py          # Layers 1–4 + 6 (rules + spatial)
│   ├── ml_detector.py        # Layer 5 (Isolation Forest)
│   ├── fusion.py             # Layer 7 (evidence fusion)
│   ├── correction.py         # Auto-correction module
│   ├── health.py             # Sensor health + maintenance prediction
│   ├── geo.py                # Haversine clustering (unchanged)
│   └── dashboard.py          # Legacy Plotly dashboard generator
└── output/
    ├── index.html            # NEW: Premium 3-tab dashboard (open this)
    ├── style.css             # NEW: Dark glassmorphism theme
    ├── app.js                # NEW: Dashboard logic (reads CSVs live)
    ├── detection_results.csv # Generated by example_usage.py
    ├── sensor_health_summary.csv
    └── evaluation_metrics.json
```

---

## Deployment Notes

### Swapping to a different data source
`real_data_loader.py` returns a standard Pandas DataFrame. To use a different data source (CSV file, SQL database, MQTT stream), replace the `fetch_open_meteo_data()` function with any function that returns a DataFrame with these columns:

```
timestamp | station_id | latitude | longitude | temperature | pressure | humidity
```

Everything downstream (pipeline, dashboard) will work without modification.

### Real-time streaming
Use `RealTimeSession` from `pipeline.py` for live ingestion:

```python
from skyguard.pipeline import RealTimeSession

session = RealTimeSession(warmup_df, buffer_size=576)

# On each new MQTT/REST tick:
results, health = session.ingest(new_snapshot_df)
```

### Adjusting detection sensitivity
- **`contamination`** in `SkyGuardPipeline(contamination=0.05)` — tells the Isolation Forest what fraction of data to treat as anomalies
- **`PHYSICAL_RANGES`** in `detectors.py` — adjust physical bounds per deployment region
- **`FROZEN_RUN_LENGTH`** in `detectors.py` — how many identical readings trigger a frozen-sensor alert
- **`MAX_RATE_OF_CHANGE`** in `detectors.py` — spike detection thresholds
