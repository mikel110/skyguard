"""
geo.py
------
Geographic utilities for spatial clustering of Automatic Weather Stations.

Stations are grouped into spatial clusters so that the spatial consistency
check (detectors.py) and spatial correction (correction.py) only compare
stations that are close enough to genuinely share weather — same synoptic
system, correlated frontal passages, typically within ~100–150 km.

Stations with fewer than `min_cluster_size` neighbors are marked isolated
and skip the spatial check entirely (graceful degradation). The 1-neighbor
case (two stations paired but no third for a robust MAD estimate) is folded
into "skip" by requiring min_cluster_size >= 3.
"""

import numpy as np


# --- Defaults (override via function args, not by editing these) -----------

MAX_SPATIAL_RADIUS_KM = 150
MIN_CLUSTER_SIZE = 3  # need >= 3 for a robust MAD; 2-point comparison is unstable


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points on Earth (km).

    Uses the Haversine formula. Accurate to ~0.5% for distances relevant
    to AWS clustering (tens to hundreds of km)."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def build_neighbor_map(station_coords, max_radius_km=MAX_SPATIAL_RADIUS_KM):
    """Build a neighbor adjacency list from station coordinates.

    Parameters
    ----------
    station_coords : dict
        station_id -> (lat, lon)
    max_radius_km : float
        Maximum distance for two stations to be considered neighbors.

    Returns
    -------
    dict : station_id -> [list of neighbor station_ids within max_radius_km]
    """
    neighbors = {}
    stations = list(station_coords.keys())
    for s1 in stations:
        neighbors[s1] = []
        lat1, lon1 = station_coords[s1]
        for s2 in stations:
            if s1 == s2:
                continue
            lat2, lon2 = station_coords[s2]
            if haversine_km(lat1, lon1, lat2, lon2) <= max_radius_km:
                neighbors[s1].append(s2)
    return neighbors


def build_spatial_clusters(station_coords, max_radius_km=MAX_SPATIAL_RADIUS_KM,
                           min_cluster_size=MIN_CLUSTER_SIZE):
    """Find connected components of stations within max_radius_km.

    Two stations in the same cluster doesn't mean they're *all* pairwise
    within max_radius_km — it means there's a chain of ≤ max_radius_km
    links connecting them (connected component of the proximity graph).

    Parameters
    ----------
    station_coords : dict
        station_id -> (lat, lon)
    max_radius_km : float
        Edge threshold for the proximity graph.
    min_cluster_size : int
        Clusters smaller than this are marked invalid (spatial check
        skipped). Must be >= 3 because a robust MAD estimate from
        only 2 points is unstable — the 1-neighbor case is explicitly
        folded into "skip."

    Returns
    -------
    cluster_map : dict
        station_id -> int cluster_id
    valid_clusters : set
        cluster_ids with >= min_cluster_size members
    neighbor_map : dict
        station_id -> [neighbor station_ids] (reused by correction.py)
    """
    neighbor_map = build_neighbor_map(station_coords, max_radius_km)

    # BFS to find connected components
    stations = list(station_coords.keys())
    visited = set()
    cluster_map = {}
    cluster_members = {}
    cluster_id = 0

    for start in stations:
        if start in visited:
            continue
        queue = [start]
        visited.add(start)
        members = []
        while queue:
            current = queue.pop(0)
            members.append(current)
            cluster_map[current] = cluster_id
            for nbr in neighbor_map[current]:
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)
        cluster_members[cluster_id] = members
        cluster_id += 1

    valid_clusters = {cid for cid, members in cluster_members.items()
                      if len(members) >= min_cluster_size}

    return cluster_map, valid_clusters, neighbor_map
