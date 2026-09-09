"""
fusion.py
---------
Combines the independent evidence streams (range, spike, frozen, drift,
multivariate physics, ML outlier score, spatial consistency) into a single
explainable verdict per observation:

    anomaly_score (0-1), confidence (0-1), severity (Low/Medium/High/Critical),
    root_cause label, and a plain-English reasoning string.

This is deliberately a transparent weighted-evidence fusion rather than a
second black-box model on top of the first -- the brief explicitly asks for
explainable reasoning, and stacking an opaque meta-model would work against
that requirement.
"""

import numpy as np
import pandas as pd

# Weights reflect how directly each signal implies a *sensor* problem
# (vs. a genuine, if unusual, weather event) -- tuned on the injected-anomaly
# demo set; recalibrate against real IMD QC-flagged history before production use.
WEIGHTS = {
    "range": 1.00,          # physically impossible -> certain fault
    "frozen": 0.85,         # very strong fault signature
    "rate": 0.55,           # spikes can occasionally be real (e.g. squall lines)
    "zscore": 0.35,         # slow drift, weaker standalone signal
    "multivariate": 0.55,   # physics-violating combination
    "ml": 0.45,             # subtle joint pattern, unlabeled
    "spatial": 0.65,        # strong signal *if* neighbors agree and disagree with this station
}

SEVERITY_BANDS = [
    (0.85, "Critical"),
    (0.65, "High"),
    (0.40, "Medium"),
    (0.15, "Low"),
]

ROOT_CAUSE_MAP = {
    "range": "Sensor fault: reading outside physically possible range",
    "frozen": "Sensor fault: value frozen (stuck sensor / comms cache)",
    "rate": "Data spike: implausible instantaneous jump",
    "zscore": "Calibration drift: sustained deviation from local climatology",
    "multivariate": "Physical inconsistency across parameters (e.g. dew point > temperature)",
    "ml": "Unclassified multivariate anomaly (novel pattern flagged by ML)",
    "spatial": "Localized fault: disagrees with neighboring stations",
}


def _severity(score):
    for threshold, label in SEVERITY_BANDS:
        if score >= threshold:
            return label
    return "Normal"


def fuse(df, param_prefix_map=("temperature", "pressure", "humidity")):
    """
    Expects df to already contain columns produced by detectors.py,
    ml_detector.py, and spatial_consistency_check. Adds fused columns:
    anomaly_score, confidence, severity, root_cause, explanation.
    """
    df = df.copy()
    n = len(df)
    per_check_max = {}

    # per-parameter checks -> take the max across the 3 parameters for each check type
    for check in ["range", "rate", "frozen", "zscore"]:
        cols = [f"{check}_{p}" for p in param_prefix_map if f"{check}_{p}" in df.columns]
        per_check_max[check] = df[cols].max(axis=1) if cols else pd.Series(0.0, index=df.index)

    per_check_max["multivariate"] = df.get("multivariate_score", pd.Series(0.0, index=df.index))
    per_check_max["ml"] = df.get("ml_score", pd.Series(0.0, index=df.index))
    per_check_max["spatial"] = df.get("spatial_score", pd.Series(0.0, index=df.index))

    evidence = pd.DataFrame(per_check_max)
    weight_vec = np.array([WEIGHTS[c] for c in evidence.columns])

    weighted_sum = (evidence.values * weight_vec).sum(axis=1)
    weight_total = weight_vec.sum()
    fused_score = np.clip(weighted_sum / weight_total, 0, 1)

    # Confidence = agreement across independent checks: if several
    # different detectors fire together we trust the verdict more than if
    # only one noisy check tripped.
    n_firing = (evidence.values > 0.3).sum(axis=1)
    agreement_bonus = np.clip(n_firing / 3.0, 0, 1)
    confidence = np.clip(0.5 * fused_score + 0.5 * agreement_bonus, 0, 1)

    df["anomaly_score"] = fused_score
    df["confidence"] = confidence
    df["severity"] = [_severity(s) for s in fused_score]
    df["is_flagged"] = df["severity"] != "Normal"

    root_causes, explanations = [], []
    for i in range(n):
        row_evidence = evidence.iloc[i]
        if row_evidence.max() <= 0.15:
            root_causes.append("none")
            explanations.append("No anomaly detected; observation consistent with expected patterns.")
            continue
        top_check = row_evidence.idxmax()
        root_causes.append(top_check)
        contributing = row_evidence[row_evidence > 0.3].sort_values(ascending=False)
        reason_parts = [f"{ROOT_CAUSE_MAP[c]} (score={v:.2f})" for c, v in contributing.items()]
        explanations.append(" | ".join(reason_parts) if reason_parts else
                             f"{ROOT_CAUSE_MAP[top_check]} (weak signal, score={row_evidence[top_check]:.2f})")

    df["root_cause"] = root_causes
    df["root_cause_label"] = df["root_cause"].map(lambda c: ROOT_CAUSE_MAP.get(c, "none"))
    df["explanation"] = explanations
    return df
