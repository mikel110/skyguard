"""
dashboard.py
------------
Builds a single self-contained interactive HTML dashboard (no server
required -- opens in any browser) covering:
  1. Per-station time series with anomalies highlighted and corrected
     values overlaid.
  2. Anomaly score timeline.
  3. Root-cause distribution across the network.
  4. Sensor health leaderboard.
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
import pandas as pd


PARAM_UNITS = {"temperature": "\u00b0C", "pressure": "hPa", "humidity": "%"}


def build_station_figure(df, station_id, param):
    g = df[df["station_id"] == station_id].sort_values("timestamp")
    flagged = g[g["is_flagged"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=g["timestamp"], y=g[param], mode="lines", name=f"{param} (raw)",
        line=dict(color="#4C78A8", width=1.5)))
    fig.add_trace(go.Scatter(
        x=g["timestamp"], y=g[f"{param}_corrected"], mode="lines",
        name="corrected", line=dict(color="#54A24B", width=1, dash="dot"),
        opacity=0.7))
    fig.add_trace(go.Scatter(
        x=flagged["timestamp"], y=flagged[param], mode="markers",
        name="flagged anomaly", marker=dict(color="#E45756", size=7, symbol="x"),
        text=flagged["root_cause_label"], hovertemplate="%{text}<br>%{y}<extra></extra>"))
    fig.update_layout(
        title=f"{station_id} - {param.title()} ({PARAM_UNITS[param]})",
        template="plotly_white", height=320, margin=dict(t=40, b=20, l=40, r=20),
        legend=dict(orientation="h", y=1.15))
    return fig


def build_score_figure(df, station_id):
    g = df[df["station_id"] == station_id].sort_values("timestamp")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=g["timestamp"], y=g["anomaly_score"], mode="lines",
                              name="anomaly score", line=dict(color="#E45756")))
    fig.add_hline(y=0.4, line_dash="dash", line_color="orange", annotation_text="Medium")
    fig.add_hline(y=0.65, line_dash="dash", line_color="red", annotation_text="High")
    fig.update_layout(title=f"{station_id} - Fused Anomaly Score", template="plotly_white",
                       height=250, margin=dict(t=40, b=20, l=40, r=20), yaxis_range=[0, 1])
    return fig


def build_root_cause_pie(df):
    counts = df[df["is_flagged"]]["root_cause_label"].value_counts().reset_index()
    counts.columns = ["root_cause", "count"]
    fig = px.pie(counts, names="root_cause", values="count", hole=0.4,
                 title="Network-wide Root Cause Distribution")
    fig.update_layout(template="plotly_white", height=380)
    return fig


def build_health_table(health_df):
    fig = go.Figure(data=[go.Table(
        header=dict(values=["Station", "Anomaly Rate (EWMA)", "Health Status",
                             "Est. Days to Maintenance", "Flagged / Total"],
                    fill_color="#4C78A8", font=dict(color="white"), align="left"),
        cells=dict(values=[
            health_df["station_id"],
            health_df["current_anomaly_rate"],
            health_df["health_status"],
            health_df["estimated_days_to_maintenance"],
            [f"{a} / {b}" for a, b in zip(health_df["total_flagged_observations"],
                                           health_df["total_observations"])],
        ], align="left"))
    ])
    fig.update_layout(title="Sensor Health & Predictive Maintenance", height=250,
                       margin=dict(t=40, b=10, l=10, r=10))
    return fig


def generate_dashboard(df, health_df, out_path="skyguard_dashboard.html",
                        stations=None, params=("temperature", "pressure", "humidity")):
    stations = stations or sorted(df["station_id"].unique())
    figs = [build_health_table(health_df), build_root_cause_pie(df)]
    for sid in stations:
        for p in params:
            figs.append(build_station_figure(df, sid, p))
        figs.append(build_score_figure(df, sid))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("<html><head><title>SkyGuard AI Dashboard</title></head><body>")
        f.write("<h1 style='font-family:sans-serif;color:#333;padding:20px 0 0 20px;'>"
                "SkyGuard AI &mdash; Real-Time AWS Anomaly Detection Dashboard</h1>")
        f.write(f"<p style='font-family:sans-serif;color:#666;padding-left:20px;'>"
                f"Generated for {len(stations)} station(s), "
                f"{len(df):,} observations, {int(df['is_flagged'].sum()):,} flagged.</p>")
        for i, fig in enumerate(figs):
            include_js = "cdn" if i == 0 else False
            f.write(fig.to_html(full_html=False, include_plotlyjs=include_js))
        f.write("</body></html>")
    return out_path
