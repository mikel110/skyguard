import pandas as pd
import requests
import time
import numpy as np
from datetime import datetime, timezone

# Use the same 7 stations from the simulator to maintain spatial consistency logic
STATIONS = [
    {"id": "AWS_DELHI",     "lat": 28.61, "lon": 77.20, "alt": 216},
    {"id": "AWS_GURGAON",   "lat": 28.46, "lon": 77.03, "alt": 217},
    {"id": "AWS_NOIDA",     "lat": 28.53, "lon": 77.39, "alt": 200},
    {"id": "AWS_KOCHI",     "lat": 9.93,  "lon": 76.26, "alt": 3},
    {"id": "AWS_BLR",       "lat": 12.97, "lon": 77.59, "alt": 920},
    {"id": "AWS_SHIMLA",    "lat": 31.10, "lon": 77.17, "alt": 2200},
    {"id": "AWS_JAISALMER", "lat": 26.91, "lon": 70.90, "alt": 225},
]

def fetch_open_meteo_data(past_days=10):
    """
    Fetches real historical weather data from Open-Meteo for the 7 demo stations.
    Returns a Pandas DataFrame formatted exactly like the SkyGuard simulator output.
    """
    print(f"Fetching {past_days} days of real weather data from Open-Meteo API...")
    
    all_data = []
    
    for station in STATIONS:
        # Open-Meteo API URL for hourly historical data
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={station['lat']}&longitude={station['lon']}"
            f"&past_days={past_days}"
            f"&hourly=temperature_2m,relative_humidity_2m,surface_pressure"
            f"&timezone=auto"
        )
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract hourly arrays
            hourly = data["hourly"]
            times = pd.to_datetime(hourly["time"])
            temps = hourly["temperature_2m"]
            humidities = hourly["relative_humidity_2m"]
            pressures = hourly["surface_pressure"]
            
            # Station altitude for MSLP conversion
            altitude_m = station.get("alt", 0)

            df_station = pd.DataFrame({
                "timestamp": times,
                "station_id": station["id"],
                "latitude": station["lat"],
                "longitude": station["lon"],
                "temperature": temps,
                "pressure": pressures,  # still surface pressure here
                "humidity": humidities,
                "is_anomaly": False,
                "anomaly_type": "none"
            })

            # ── Convert surface pressure → MSLP ─────────────────────────────
            # All real AWS networks transmit pressure reduced to Mean Sea Level
            # so readings are directly comparable across stations at different
            # altitudes. The barometric formula:
            #   MSLP ≈ P_station * exp(g * h / (R_d * T_avg))
            # where T_avg is temperature in Kelvin. This matches the
            # standard IMD / WMO reduction used on real AWS networks.
            if altitude_m > 0:
                T_k = df_station["temperature"] + 273.15
                df_station["pressure"] = df_station["pressure"] * np.exp(
                    9.80665 * altitude_m / (287.05 * T_k)
                )
            
            all_data.append(df_station)
            
            # Be polite to the free API
            time.sleep(0.2)
            
        except Exception as e:
            print(f"Failed to fetch data for {station['id']}: {e}")
            
    if not all_data:
        raise RuntimeError("Failed to fetch data from Open-Meteo for all stations.")
        
    # Combine all stations
    full_df = pd.concat(all_data, ignore_index=True)
    
    # Forward fill any missing API values (NaNs) just in case
    full_df = full_df.ffill().bfill()
    
    # Cast to float to avoid pandas upcasting errors during anomaly injection
    full_df["temperature"] = full_df["temperature"].astype(float)
    full_df["pressure"] = full_df["pressure"].astype(float)
    full_df["humidity"] = full_df["humidity"].astype(float)
    
    # Sort chronologically, then by station
    full_df = full_df.sort_values(by=["timestamp", "station_id"]).reset_index(drop=True)

    # ── Strip future timestamps ──────────────────────────────────────────────
    # Open-Meteo's /v1/forecast endpoint returns historical data PLUS the next
    # 7-day forecast window in the same response. We keep only rows up to the
    # current UTC moment so the dashboard never shows "future" readings.
    now_utc = pd.Timestamp.utcnow().tz_localize(None)  # naive UTC for comparison
    if full_df["timestamp"].dt.tz is not None:
        # timestamps are tz-aware — convert now to match
        now_compare = pd.Timestamp.utcnow()
    else:
        now_compare = now_utc
    full_df["timestamp"] = full_df["timestamp"].dt.tz_localize(None) if full_df["timestamp"].dt.tz is not None else full_df["timestamp"]
    full_df = full_df[full_df["timestamp"] <= now_utc].reset_index(drop=True)
    # ────────────────────────────────────────────────────────────────────────

    print(f"Successfully loaded {len(full_df)} real-world observations (past only).")
    return full_df
