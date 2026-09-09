"""
example_usage.py
=================
Fully executable demonstration of SkyGuard AI.

Run:
    python3 example_usage.py

What it does:
  1. Simulates a 7-station AWS network (10 days @ 5-min cadence) with
     labeled anomalies injected (spikes, frozen sensors, drift, comms
     dropouts, multivariate inconsistency, noise bursts). Includes a
     3-station Delhi NCR cluster for spatial check + 4 isolated stations.
  2. Runs the full detection pipeline (rules + ML + spatial + fusion).
  3. Evaluates detection accuracy against the injected ground truth.
  4. Prints a worked example matching the brief's use case (55C reading
     while neighbors are normal).
  5. Runs a short real-time streaming simulation, tick by tick.
  6. Writes an interactive HTML dashboard + CSV outputs to ./output/
"""

import os
import json
import numpy as np
import pandas as pd
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix

from skyguard.simulator import build_demo_dataset
from skyguard.pipeline import SkyGuardPipeline, RealTimeSession
from skyguard.dashboard import generate_dashboard

OUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUT_DIR, exist_ok=True)


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


from skyguard.real_data_loader import fetch_open_meteo_data
from skyguard.simulator import AnomalyInjector

def step1_simulate():
    section("STEP 1: Fetching Real Data (Open-Meteo) & Injecting Anomalies")
    
    # Fetch real historical data
    df = fetch_open_meteo_data(past_days=10)
    
    # Inject ground truth anomalies so we can evaluate the pipeline
    injector = AnomalyInjector(seed=42)
    df = injector.inject(df, n_events_per_station=8)
    
    print(f"Generated {len(df):,} observations across {df['station_id'].nunique()} stations.")
    print(f"Injected ground-truth anomalies: {int(df['is_anomaly'].sum()):,} "
          f"({100*df['is_anomaly'].mean():.2f}% of records).")
    print("\nAnomaly type breakdown (ground truth):")
    print(df.loc[df.is_anomaly, "anomaly_type"].value_counts().to_string())
    return df


def step2_detect(df):
    section("STEP 2: Running SkyGuard detection pipeline")
    pipeline = SkyGuardPipeline(contamination=0.05)
    results, health = pipeline.run(df, apply_correction=True)
    print(f"Flagged {int(results['is_flagged'].sum()):,} observations as anomalous "
          f"({100*results['is_flagged'].mean():.2f}% of records).")
    return pipeline, results, health


def _score_tier(results, y_pred, label):
    y_true = results["is_anomaly"].astype(int)
    p = precision_score(y_true, y_pred, zero_division=0)
    r = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    print(f"\n[{label}]")
    print(f"  Precision: {p:.3f}   Recall: {r:.3f}   F1-score: {f1:.3f}")
    print(f"  TP={tp}  FP={fp}  FN={fn}  TN={tn}   "
          f"False alarm rate (of normal data): {fp/(fp+tn):.3%}")
    return {"precision": p, "recall": r, "f1": f1, "tp": int(tp), "fp": int(fp),
            "fn": int(fn), "tn": int(tn)}


def step3_evaluate(results):
    section("STEP 3: Evaluating detection accuracy against injected ground truth")
    print("SkyGuard reports two-tier severity by design, precisely to control false "
          "alarms without hiding weak evidence: 'Low' severity = advisory / worth a QC "
          "review, 'Medium or above' = confident alert. We report both operating points.")

    any_flag = results["is_flagged"].astype(int)
    confirmed = results["severity"].isin(["Medium", "High", "Critical"]).astype(int)

    tier_any = _score_tier(results, any_flag, "Tier 1: Any flag (Low+) -- high-recall QC screen")
    tier_confirmed = _score_tier(results, confirmed, "Tier 2: Confirmed alert (Medium+) -- low-alarm-fatigue operational alert")

    print("\nRecall by injected anomaly type (Tier 1 - any flag):")
    for kind, g in results[results["is_anomaly"]].groupby("anomaly_type"):
        recall_k = g["is_flagged"].mean()
        print(f"  {kind:30s} recall={recall_k:.3f}  (n={len(g)})")

    metrics = {"tier1_any_flag": tier_any, "tier2_confirmed_alert": tier_confirmed}
    with open(os.path.join(OUT_DIR, "evaluation_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)
    return metrics


def step4_worked_example(pipeline, results):
    section("STEP 4: Worked example (matches the brief's use case)")
    mv_hits = results[(results["anomaly_type"] == "multivariate_inconsistency") &
                       (results["is_flagged"])]
    if len(mv_hits) == 0:
        print("No multivariate_inconsistency event was flagged in this run; skipping.")
        return
    example = mv_hits.iloc[len(mv_hits) // 2]
    idx = example.name
    explanation = pipeline.explain_record(results, idx)
    print("A station reported an implausible temperature/humidity/pressure combination "
          "while the situation was checked against neighboring stations.\n")
    print(json.dumps(explanation, indent=2, default=str))


def step5_realtime_demo(df):
    section("STEP 5: Real-time streaming simulation (tick-by-tick)")
    stations = df["station_id"].unique()
    warmup_cutoff = df["timestamp"].quantile(0.7)
    warmup = df[df["timestamp"] <= warmup_cutoff]
    stream = df[df["timestamp"] > warmup_cutoff].sort_values("timestamp")

    session = RealTimeSession(warmup, buffer_size=576, contamination=0.05)
    print(f"Warm-started on {len(warmup):,} historical observations. "
          f"Streaming {stream['timestamp'].nunique():,} new timestamps...")

    alerts_emitted = 0
    for i, (ts, snapshot) in enumerate(stream.groupby("timestamp")):
        tick_results, health = session.ingest(snapshot)
        flagged = tick_results[tick_results["is_flagged"]]
        if len(flagged):
            alerts_emitted += len(flagged)
            if alerts_emitted <= 15:  # keep console output readable
                for _, row in flagged.iterrows():
                    print(f"  [ALERT] {ts}  {row['station_id']:14s} "
                          f"severity={row['severity']:8s} cause={row['root_cause_label']}")
        if i >= 60:  # demo cap so the script finishes quickly
            break
    print(f"\nReal-time demo processed {i+1} timestamps; emitted {alerts_emitted} alerts.")


def step6_outputs_and_dashboard(results, health):
    section("STEP 6: Writing outputs and dashboard")
    results_path = os.path.join(OUT_DIR, "detection_results.csv")
    health_path = os.path.join(OUT_DIR, "sensor_health_summary.csv")
    dash_path = os.path.join(OUT_DIR, "skyguard_dashboard.html")

    export_cols = ["timestamp", "station_id", "temperature", "temperature_corrected",
                   "pressure", "pressure_corrected", "humidity", "humidity_corrected",
                   "anomaly_score", "confidence", "severity", "root_cause_label",
                   "is_flagged", "is_anomaly", "anomaly_type"]
    results[export_cols].to_csv(results_path, index=False)
    health.to_csv(health_path, index=False)
    generate_dashboard(results, health, out_path=dash_path)

    print(f"Detection results -> {results_path}")
    print(f"Sensor health summary -> {health_path}")
    print(f"Interactive dashboard -> {dash_path}")
    print("\nSensor health leaderboard:")
    print(health.to_string(index=False))


if __name__ == "__main__":
    df = step1_simulate()
    pipeline, results, health = step2_detect(df)
    step3_evaluate(results)
    step4_worked_example(pipeline, results)
    step5_realtime_demo(df)
    step6_outputs_and_dashboard(results, health)

    section("DONE")
    print("Open output/skyguard_dashboard.html in a browser to explore results visually.")
