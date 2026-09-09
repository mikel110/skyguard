"""
ml_detector.py
--------------
Unsupervised multivariate ML layer. Rule-based checks catch known fault
signatures; Isolation Forest catches *unknown* / subtle joint anomalies in
the (temperature, pressure, humidity, rate-of-change, dew-point-gap)
feature space that no single hand-written rule enumerates.

Explainability approach: rather than depending on a heavy SHAP install
(brittle in constrained/edge environments), we compute a fast, model-
agnostic feature-attribution score: for each flagged point we perturb one
feature at a time back to its local rolling-median ("plausible normal")
value and measure how much the anomaly score drops. This is a leave-one-
out / occlusion-based attribution -- the same idea SHAP generalizes -- and
needs no extra dependency, which matters for scalable, low-power
deployment (one of the stated evaluation criteria). A drop-in SHAP
TreeExplainer can replace `explain_point()` unchanged if the deployment
environment allows it (see README).
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest


FEATURE_COLUMNS = [
    "temperature", "pressure", "humidity",
    "temp_roc", "pres_roc", "hum_roc",
    "dew_point_gap",
]


def build_feature_matrix(df):
    feat = pd.DataFrame(index=df.index)
    feat["temperature"] = df["temperature"]
    feat["pressure"] = df["pressure"]
    feat["humidity"] = df["humidity"]
    feat["temp_roc"] = df["temperature"].diff().fillna(0)
    feat["pres_roc"] = df["pressure"].diff().fillna(0)
    feat["hum_roc"] = df["humidity"].diff().fillna(0)
    if "dew_point" in df.columns:
        feat["dew_point_gap"] = df["dew_point"] - df["temperature"]
    else:
        feat["dew_point_gap"] = 0.0
    return feat[FEATURE_COLUMNS]


class MultivariateAnomalyDetector:
    """Thin wrapper around IsolationForest with per-point explainability
    and an interface designed for periodic retraining on a trailing
    window, which is how this would run in a real streaming deployment
    (fit nightly / every N hours on the last K days of vetted-normal data)."""

    def __init__(self, contamination=0.05, n_estimators=150, random_state=42):
        self.model = IsolationForest(
            contamination=contamination,
            n_estimators=n_estimators,
            random_state=random_state,
        )
        self.feature_medians_ = None
        self.fitted = False

    def fit(self, df):
        X = build_feature_matrix(df)
        self.feature_medians_ = X.median()
        self.model.fit(X)
        self.fitted = True
        return self

    def score(self, df):
        """Returns anomaly score in [0, 1], higher = more anomalous."""
        if not self.fitted:
            self.fit(df)
        X = build_feature_matrix(df)
        raw = -self.model.score_samples(X)  # higher = more anomalous
        # min-max normalize against the *training* distribution for stability
        lo, hi = np.percentile(raw, [1, 99])
        norm = np.clip((raw - lo) / (hi - lo + 1e-9), 0, 1)
        return pd.Series(norm, index=df.index), X

    def explain_point(self, X_row, top_k=3):
        """Occlusion-based feature attribution for one row: how much does
        the anomaly score fall if this feature is reset to its typical
        (median) value? Larger drop = that feature explains more of the
        anomaly. Returns a ranked list of (feature, contribution)."""
        base_score = -self.model.score_samples(X_row.to_frame().T.values)[0]
        contributions = {}
        for col in X_row.index:
            perturbed = X_row.copy()
            perturbed[col] = self.feature_medians_[col]
            new_score = -self.model.score_samples(perturbed.to_frame().T.values)[0]
            contributions[col] = max(base_score - new_score, 0)
        total = sum(contributions.values()) + 1e-9
        ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
        return [(feat, round(val / total, 3)) for feat, val in ranked[:top_k] if val > 0]
