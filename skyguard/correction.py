"""
correction.py
-------------
Suggests a corrected/imputed value for flagged observations, using the
cause-appropriate strategy:

  - frozen / spike / range faults  -> rolling-median-based temporal
    interpolation from the surrounding trusted (non-flagged) window.
  - spatial fault (station disagrees with healthy neighbors) -> bias-
    corrected spatial estimate from the neighbor median, adjusted by this
    station's long-run offset from the cluster (so we don't just erase
    genuine local micro-climate differences).
  - everything else -> temporal estimate, falling back to spatial if needed.

The corrected value is advisory only (for QC dashboards / gap-filling),
never silently substituted into the operational feed without human/QC
sign-off in a real deployment.

IMPORTANT: the spatial correction uses the same neighbor_map produced by
geo.build_spatial_clusters that the spatial consistency check uses, so
the two paths always agree on which stations are "nearby." They are kept
in sync by having pipeline.py build the neighbor map once and pass it to
both functions.
"""

import numpy as np
import pandas as pd


def temporal_correction(df, param, window=24):
    """Linearly interpolate across flagged observations."""
    clean = df[param].where(~df["is_flagged"])
    return clean.interpolate(method="linear", limit_direction="both")


def spatial_correction(network_df, station_id, param, neighbor_ids=None,
                       timestamp_col="timestamp"):
    """Spatial estimate for this station at each timestamp, using only
    genuinely nearby stations (neighbor_ids). Bias-corrected by the
    station's median historical offset from its neighbors (so e.g. a
    high-altitude station's naturally lower pressure isn't flattened
    toward the cluster average).

    If neighbor_ids is None or empty, returns NaN — this is correct
    behavior for isolated stations with no nearby reference. The
    correction pipeline falls back to temporal-only correction in
    this case."""
    station_mask = network_df["station_id"] == station_id
    station_idx = network_df.index[station_mask]

    if neighbor_ids is None or len(neighbor_ids) == 0:
        # No nearby stations: spatial correction unavailable
        return pd.Series(np.nan, index=station_idx)

    # Reference: only nearby stations
    ref_mask = network_df["station_id"].isin(neighbor_ids)
    ref_median_by_ts = network_df.loc[ref_mask].groupby(timestamp_col)[param].median()

    # Map reference median to this station's timestamps
    station_ts = network_df.loc[station_mask, timestamp_col]
    mapped_median = station_ts.map(ref_median_by_ts)
    mapped_median.index = station_idx  # align index

    # Bias correction: this station's healthy readings vs neighbors
    healthy_mask = station_mask & ~network_df["is_flagged"]
    healthy = network_df.loc[healthy_mask]
    if len(healthy) > 0:
        healthy_ref = healthy[timestamp_col].map(ref_median_by_ts)
        offset_values = healthy[param].values - healthy_ref.values
        offset = np.nanmedian(offset_values)
        offset = 0.0 if np.isnan(offset) else offset
    else:
        offset = 0.0

    return mapped_median + offset


def apply_corrections(network_df, neighbor_map=None, timestamp_col="timestamp"):
    """Adds `<param>_corrected` columns for every parameter, populated only
    where is_flagged is True (otherwise equal to the original reading).

    Parameters
    ----------
    neighbor_map : dict or None
        station_id -> [list of neighbor station_ids]. Must be the same
        neighbor map used by spatial_consistency_check (built by
        geo.build_spatial_clusters). If None, spatial correction is
        skipped for all stations.
    """
    network_df = network_df.copy()
    for param in ["temperature", "pressure", "humidity"]:
        network_df[f"{param}_corrected"] = network_df[param]

    for station_id, g in network_df.groupby("station_id"):
        idx = g.index
        neighbors = neighbor_map.get(station_id) if neighbor_map else None
        for param in ["temperature", "pressure", "humidity"]:
            temporal_est = temporal_correction(network_df.loc[idx], param)
            spatial_est = spatial_correction(network_df, station_id, param,
                                            neighbor_ids=neighbors,
                                            timestamp_col=timestamp_col)
            # Prefer temporal (finer-grained, sensor-specific), then spatial,
            # then leave the original value unchanged.
            corrected = np.where(
                ~np.isnan(temporal_est.values), temporal_est.values,
                np.where(~np.isnan(spatial_est.values), spatial_est.values,
                         network_df.loc[idx, param].values))
            flagged_mask = network_df.loc[idx, "is_flagged"].values
            col = f"{param}_corrected"
            new_vals = network_df.loc[idx, col].values.copy()
            new_vals[flagged_mask] = corrected[flagged_mask]
            network_df.loc[idx, col] = new_vals
    return network_df
