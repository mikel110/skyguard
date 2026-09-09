# SkyGuard AI
### Intelligent Real-Time Anomaly Detection for Temperature, Pressure & Humidity Sensors in Automatic Weather Stations
**Prepared for: India Meteorological Department — AWS Data Quality Challenge**

---

## 1. What this delivers

A working, runnable anomaly-detection system for AWS temperature/pressure/humidity streams, built as a Python package (`skyguard/`) plus a demonstration script (`example_usage.py`). It is not a single model — it's a **layered QC pipeline** that mirrors how operational meteorological QC actually works (WMO-style gross-range / internal-consistency / temporal / spatial checks), augmented with an unsupervised ML layer for the anomalies that fixed rules can't enumerate.

```
skyguard/
├── simulator.py      # synthetic AWS network generator + labeled anomaly injector
├── detectors.py       # rule-based checks: range, spike, frozen, drift, dew-point physics, spatial
├── ml_detector.py      # Isolation Forest multivariate detector + occlusion-based explainability
├── fusion.py         # weighted evidence fusion -> score, confidence, severity, root cause
├── correction.py       # temporal + spatial corrected-value estimation
├── health.py         # EWMA sensor health tracking + naive predictive-maintenance estimate
├── pipeline.py        # orchestration: batch mode + real-time streaming session
└── dashboard.py        # self-contained interactive HTML dashboard (Plotly)
example_usage.py        # end-to-end runnable demo (simulate -> detect -> evaluate -> visualize)
```

Run it with:
```bash
pip install -r requirements.txt
python3 example_usage.py
```
This produces, in `output/`: `detection_results.csv`, `sensor_health_summary.csv`, `evaluation_metrics.json`, and `skyguard_dashboard.html` (open directly in a browser).

**No real IMD dataset was available for this submission**, so `simulator.py` generates a realistic 5-station regional network (10 days, 5-minute cadence, correlated diurnal + synoptic weather signal across stations) and injects six labeled fault types so detection quality can be measured objectively. **Swap `NetworkSimulator.generate()` for a real AWS data loader (CSV/database/MQTT feed) and every downstream stage is unchanged** — this is the intended integration point.

---

## 2. Why a layered design, not one model

A single black-box classifier trained on historical AWS data has three problems for this use case: it needs labeled fault data most AWS networks don't have; it can't explain *why* a point is anomalous, which the brief explicitly requires; and it conflates "sensor fault" with "genuinely unusual but real weather," which is exactly the distinction the brief asks the system to make.

Instead, SkyGuard runs several **independent, individually explainable evidence sources** and fuses them transparently:

| Layer | Catches | Why it's explainable |
|---|---|---|
| **Range check** | Physically impossible readings (sensor failure, corrupted packet) | Direct threshold, trivial to explain |
| **Spike / rate-of-change check** | Sudden implausible jumps | Robust (MAD-based) z-score of the reading's first difference |
| **Frozen-value check** | Stuck sensor / cached/stale value / comms latch | Run-length of unchanged readings vs. a plausible-noise floor |
| **Rolling z-score (drift) check** | Calibration drift, slow bias creep | Deviation from a 24-hour rolling climatology (captures the full diurnal cycle so normal day/night swing isn't mistaken for drift) |
| **Multivariate physics check** | Implausible parameter combinations | Dew point (Magnus-Tetens formula) can never exceed air temperature — a hard thermodynamic constraint; also flags simultaneous sharp pressure+humidity jumps |
| **Spatial consistency check** | Localized single-station faults | Compares each station's *detrended residual* against its neighbors' residuals at the same instant — directly implements the brief's worked example (55°C at one station while neighbors are normal) |
| **ML multivariate detector** | Novel/subtle joint anomalies rules don't enumerate | Isolation Forest, with per-point feature attribution via occlusion (explained below) |

A transparent weighted-sum fusion (`fusion.py`) combines these into an `anomaly_score`, `confidence` (based on how many independent checks agree), a `severity` label, and a `root_cause` — with the full evidence breakdown always retained.

---

## 3. Explainable AI approach

The brief asks for SHAP/LIME-style reasoning. Rather than depending on the full SHAP library (which adds heavy compile-time dependencies that work against the "scalable / edge-deployable" evaluation criterion), SkyGuard implements the same underlying idea natively:

- **Rule-based checks are explainable by construction** — each one *is* a plain-English reason (e.g. "reading outside physically possible range", "value frozen for 65 minutes").
- **The ML layer uses occlusion-based feature attribution**: for a flagged point, each feature is reset to its typical (median) value one at a time, and the resulting drop in the anomaly score is measured. This is the same intuition SHAP generalizes (Shapley-value attribution), but needs no extra dependency and runs in milliseconds — practical for constrained or embedded deployment. `ml_detector.explain_point()` is a drop-in replacement point for a full `shap.TreeExplainer` if a deployment environment can afford it.
- Every flagged record carries a `root_cause_label` and a human-readable `explanation` string listing every contributing check and its score — this is what a forecaster or QC analyst would actually read.

---

## 4. Severity tiers — deliberately controlling false alarms

The brief's evaluation criteria explicitly weigh minimizing false alarms. Rather than a single flag/no-flag decision, SkyGuard reports **two operating tiers**, and the demo script evaluates both:

- **Tier 1 — "Low+" (any flag):** a high-recall QC screen, useful for a dashboard where a human reviews soft evidence.
- **Tier 2 — "Medium+" (confirmed alert):** what should actually page an operator. On the injected-anomaly demo dataset this tier reaches **~99% precision with a ~0.02% false-alarm rate**, at the cost of catching only the more clear-cut ~26% of injected anomalies outright — the rest surface at Tier 1 for review rather than being missed silently.

This two-tier design is the direct, concrete answer to "distinguish genuine meteorological events from sensor/data anomalies while minimizing false alarms": genuinely unusual-but-real weather rarely trips *multiple independent* checks at once (a real heatwave doesn't also disagree with dew-point physics or freeze mid-reading), so requiring multi-check agreement for a Tier-2 alert is what suppresses false alarms on real extreme weather.

**On the injected demo dataset** (14,400 observations, 5 stations, 6 fault types):

| Tier | Precision | Recall | F1 | False alarm rate |
|---|---|---|---|---|
| Tier 1 (any flag) | 0.40 | 0.78 | 0.53 | 5.7% |
| Tier 2 (confirmed) | 0.99 | 0.26 | 0.41 | 0.02% |

Recall by fault type (Tier 1) ranges from ~74–100% for spikes, dropouts, drift, and multivariate inconsistency down to ~42% for very short frozen-sensor events (by design — a value unchanged for under ~40 minutes is statistically plausible for a slowly-varying parameter and isn't hard-flagged, trading a bit of recall for fewer false alarms on genuinely calm periods). All thresholds in `detectors.py` and `fusion.py` are named constants meant to be recalibrated against real IMD QC-flagged history before production use — the demo numbers characterize the *method*, not a claim about real AWS data.

---

## 5. Corrected value estimation

For every flagged observation, `correction.py` proposes a corrected value (advisory, not auto-substituted into the operational feed):
- **Temporal estimate:** rolling median of the surrounding *non-flagged* readings from the same sensor.
- **Spatial estimate:** the network median at that instant, bias-corrected by this station's own long-run offset from the network (so a genuinely cooler high-altitude station isn't flattened toward the network average).
- The two are blended, preferring the temporal estimate when available since it's sensor-specific.

## 6. Sensor health & predictive maintenance

`health.py` tracks an exponentially-weighted anomaly rate per station (half-life ~8 hours) and classifies it into Healthy / Watch / Degraded / Faulty bands. A naive linear-trend extrapolation projects the current trajectory forward to estimate days-to-maintenance. This is intentionally a transparent heuristic rather than a survival-analysis model, since most AWS networks don't have labeled failure histories to train one — it's built as the seam where a proper Remaining-Useful-Life model would plug in once failure logs exist.

## 7. Real-time capability

`pipeline.RealTimeSession` demonstrates the streaming shape: models are warm-started on historical data, then `ingest()` scores one new network-wide timestamp at a time against a rolling buffer, only periodically retraining the Isolation Forest in the background (mirroring how a live MQTT/Kafka deployment would run continuous scoring with scheduled — not per-tick — model refresh). The rule-based layer is O(window size) per station per tick, and the ML layer's inference is sub-millisecond per point.

## 8. Scalability & deployability

- Every detector operates **per-station independently** except the spatial check, so the pipeline parallelizes trivially across a large national network (one process/thread per station, or a distributed stream-processing job per station-partition).
- The rule-based layer has **no training dependency** and can run standalone on constrained hardware.
- **Edge AI note (ESP32):** the Isolation Forest and Python stack are not suitable for direct ESP32 deployment. The recommended split is: run the full pipeline (rules + ML + spatial + fusion) centrally/on a gateway, and push down only the **range, spike, and frozen-value rule thresholds** (a few comparisons and a small circular buffer) to run natively on the ESP32 as a first-line filter — catching the most common hard faults (out-of-range, stuck sensor) locally with near-zero power/compute cost, while the richer multivariate/spatial/ML analysis runs centrally where full context (the whole network's data) is actually available. This hybrid split is the practical answer to "Edge AI for low-power deployment on ESP32" — pushing the full ML model to a microcontroller would cost far more energy/complexity than it buys, when the hardest anomalies (multivariate, spatial) inherently need network-wide context an edge node doesn't have.

## 9. Visualization

`dashboard.py` generates one self-contained interactive HTML file (open in any browser, no server needed) with: per-station time series (raw + corrected values + flagged points), the fused anomaly score timeline, a network-wide root-cause breakdown, and a sensor-health/predictive-maintenance leaderboard.

---

## 10. Example use cases

**A. The brief's worked example** — a station reports 55°C with extreme humidity and an abnormal pressure swing while neighbors are normal. Reproduced in `example_usage.py` (Step 4): the multivariate physics check flags the dew-point violation, the spatial check flags disagreement with neighbors, and the ML layer's feature attribution confirms which parameters drove the anomaly — the same shape of output a forecaster would see for a real event.

**B. Frozen sensor after a power brownout** — a station's pressure sensor caches its last reading during a power fluctuation. The frozen-value check catches the stuck run once it exceeds a plausible duration; `health.py` accumulates this into a rising anomaly rate and eventually a "Degraded — schedule maintenance" status even before the fault becomes obvious in raw data.

**C. Gradual calibration drift** — a humidity sensor drifts +0.5%/day over weeks. Single-reading checks miss this entirely; the 24-hour rolling z-score check catches the accumulating deviation from local climatology well before it would bias a monthly climate summary.

**D. Communication dropout / corrupted packet** — a garbled transmission produces values that are each individually in-range but jointly inconsistent (e.g. plausible temperature paired with a physically incoherent dew point). Caught by the multivariate consistency check even though no single-parameter threshold fires.

**E. Genuine severe weather (the negative control)** — a real squall line produces a fast, large pressure drop at one station. Because it (a) doesn't violate dew-point physics, (b) is *not* frozen, and (c) — critically — shows up correlated at neighboring stations too, it does not trigger a Tier-2 spatial-fault alert, correctly distinguishing it from a sensor fault.

**F. Network-wide QC dashboard for a regional met office** — an operator opens `skyguard_dashboard.html` each morning to review overnight Tier-1 flags across all stations, using the corrected-value overlay and root-cause labels to triage which readings need a field visit vs. which were self-evidently real weather.

---

## 11. Mapping to the stated evaluation criteria

| Criterion | Where addressed |
|---|---|
| Innovation & Novelty | Detrended spatial-consistency check (residual-based, not raw-value); occlusion-based explainability without a heavy SHAP dependency; two-tier severity fusion designed explicitly to control false alarms |
| Detection Accuracy | Section 4, `evaluation_metrics.json`; measured against labeled injected ground truth, both tiers reported honestly |
| Real-Time Capability | `pipeline.RealTimeSession`, Section 7 |
| Explainability | Section 3; every flagged record carries a full evidence breakdown |
| Scalability | Per-station-independent architecture, Section 8 |
| Practical Deployability | Config constants are the single source of truth for recalibration; clean swap-in point for a real data loader |
| Visualization / UI | `dashboard.py`, Section 9 |
| Energy Efficiency | Edge/central hybrid split proposal, Section 8 |

## 12. Honest limitations & next steps

- Thresholds are tuned against the synthetic demo, not real IMD QC-flagged history — recalibration against real labeled data is the single highest-value next step before deployment.
- The Isolation Forest is retrained per station on a trailing window; a production system should schedule this retraining explicitly (e.g. hourly, off the request path) rather than inline as in the simplified demo loop.
- Frozen-value and drift thresholds trade recall for false-alarm control on short/subtle events by design — these are named, documented constants meant to be tuned per parameter and per region's known sensor behavior.
- The predictive-maintenance estimate is a transparent trend heuristic, not a trained model — it should be replaced with a proper model once real failure/maintenance logs are available to train on.
