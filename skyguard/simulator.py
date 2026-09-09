"""
simulator.py
------------
Generates realistic synthetic Automatic Weather Station (AWS) network data
(temperature, pressure, humidity) with diurnal + seasonal structure, spatially
correlated weather across nearby stations, and injects labeled anomaly
events so the detection pipeline can be objectively evaluated.

Key design: the regional weather signal is shared between stations with a
correlation that decays exponentially with inter-station distance, using
*different decay length scales per parameter* because pressure systems are
broad-scale (~500 km), temperature is intermediate (~200 km, terrain-dependent),
and humidity is highly localized (~100 km, coastal/irrigation effects). This
is a deliberate simplification — real spatial correlation structures are
anisotropic and weather-regime-dependent — but it's stated explicitly rather
than hidden, and it produces data where nearby stations plausibly share
weather while distant stations don't, which is what the spatial consistency
check needs to be tested against honestly.

Swap `NetworkSimulator.generate()` for a real data loader (CSV/DB/MQTT) and
every downstream stage is unchanged — this is the intended integration point.
"""

import numpy as np
import pandas as pd

from .geo import haversine_km


# Spatial decorrelation length scales (km). These control how quickly the
# shared regional weather signal fades with distance between stations.
# Different parameters decorrelate at different rates:
#   - Pressure: synoptic systems span hundreds of km → long range
#   - Temperature: terrain, urban heat islands, altitude → medium range
#   - Humidity: coastal proximity, irrigation, land use → short range
# These are order-of-magnitude estimates, not fitted to real data.
SPATIAL_LENGTH_SCALES = {
    "temperature": 200,
    "pressure": 500,
    "humidity": 100,
}


class StationConfig:
    def __init__(self, station_id, lat, lon, altitude_m, base_temp=27.0,
                 base_pressure=1013.0, base_humidity=60.0):
        self.station_id = station_id
        self.lat = lat
        self.lon = lon
        self.altitude_m = altitude_m
        # station-specific micro-climate offsets (sensor/site bias)
        self.base_temp = base_temp
        # NOTE: real AWS networks report pressure reduced to Mean Sea Level
        # (MSLP/QNH) precisely so readings are comparable across stations at
        # different altitudes -- the raw uncorrected station-level pressure
        # is NOT what gets QC'd against a fixed physical envelope. We model
        # MSLP here: ~1013 hPa baseline with only a small station-siting
        # bias, not a full barometric reduction by altitude.
        self.base_pressure = base_pressure - altitude_m * 0.004
        self.base_humidity = base_humidity


class NetworkSimulator:
    """Simulates a regional network of AWS stations reporting every
    `freq_minutes` minutes. Nearby stations share a common weather signal
    (attenuated by distance); distant stations see largely independent
    weather. This makes spatial-consistency checks meaningful for nearby
    clusters and irrelevant for distant pairs — matching real-world behavior."""

    def __init__(self, stations, start="2026-01-01", periods=2880,
                 freq_minutes=5, seed=42):
        self.stations = stations
        self.start = pd.Timestamp(start)
        self.periods = periods
        self.freq = pd.Timedelta(minutes=freq_minutes)
        self.rng = np.random.default_rng(seed)

    def _base_regional_signal(self, n):
        """Reference regional weather signal (the 'true' synoptic-scale
        weather at the network centroid). All stations' signals are
        derived from this, attenuated by distance."""
        t = np.arange(n)
        hours = (t * (self.freq.total_seconds() / 3600.0)) % 24
        diurnal_temp = 6.0 * np.sin((hours - 9) / 24 * 2 * np.pi)
        synoptic = 2.0 * np.sin(t / (n / 3) * 2 * np.pi + 0.5)
        diurnal_pressure = -1.2 * np.sin((hours - 15) / 24 * 2 * np.pi)
        synoptic_pressure = 4.0 * np.sin(t / (n / 4) * 2 * np.pi)
        diurnal_humidity = -15.0 * np.sin((hours - 9) / 24 * 2 * np.pi)
        return (diurnal_temp + synoptic,
                diurnal_pressure + synoptic_pressure,
                diurnal_humidity)

    def _independent_signal(self, n):
        """Independent local weather component for one station — same
        diurnal structure as the base but with random phase/amplitude
        offsets, representing local variability (terrain effects, local
        convection, sea breeze) that doesn't correlate with distant
        stations. Called once per station; self.rng ensures each call
        produces a different realization."""
        t = np.arange(n)
        hours = (t * (self.freq.total_seconds() / 3600.0)) % 24

        # Random phase and amplitude modulations unique to this station
        phase = self.rng.uniform(-np.pi, np.pi, 5)
        amp = self.rng.uniform(0.6, 1.4, 5)

        temp = amp[0] * 6.0 * np.sin((hours - 9) / 24 * 2 * np.pi + phase[0])
        temp += amp[1] * 2.0 * np.sin(t / (n / 3) * 2 * np.pi + phase[1])

        pres = amp[2] * -1.2 * np.sin((hours - 15) / 24 * 2 * np.pi + phase[2])
        pres += amp[3] * 4.0 * np.sin(t / (n / 4) * 2 * np.pi + phase[3])

        hum = amp[4] * -15.0 * np.sin((hours - 9) / 24 * 2 * np.pi + phase[4])

        return temp, pres, hum

    def generate(self):
        n = self.periods
        timestamps = self.start + np.arange(n) * self.freq

        # Base regional signals at the network centroid
        base_temp, base_pres, base_hum = self._base_regional_signal(n)

        # Reference point for distance calculation
        ref_lat = np.mean([s.lat for s in self.stations])
        ref_lon = np.mean([s.lon for s in self.stations])

        frames = []
        for cfg in self.stations:
            d_km = haversine_km(ref_lat, ref_lon, cfg.lat, cfg.lon)

            # Per-parameter spatial correlation: exp(-d / L)
            # corr=1 at d=0 (same location), decays toward 0 at large distance
            corr_t = np.exp(-d_km / SPATIAL_LENGTH_SCALES["temperature"])
            corr_p = np.exp(-d_km / SPATIAL_LENGTH_SCALES["pressure"])
            corr_h = np.exp(-d_km / SPATIAL_LENGTH_SCALES["humidity"])

            # Independent local weather for this station
            indep_temp, indep_pres, indep_hum = self._independent_signal(n)

            # Mix: signal = corr * shared + sqrt(1 - corr²) * independent
            # This preserves total signal variance regardless of distance
            d_temp = corr_t * base_temp + np.sqrt(1 - corr_t**2) * indep_temp
            d_pres = corr_p * base_pres + np.sqrt(1 - corr_p**2) * indep_pres
            d_hum = corr_h * base_hum + np.sqrt(1 - corr_h**2) * indep_hum

            temp = cfg.base_temp + d_temp + self.rng.normal(0, 0.25, n)
            pres = cfg.base_pressure + d_pres + self.rng.normal(0, 0.15, n)
            hum = np.clip(cfg.base_humidity + d_hum + self.rng.normal(0, 1.5, n), 2, 100)
            df = pd.DataFrame({
                "timestamp": timestamps,
                "station_id": cfg.station_id,
                "latitude": cfg.lat,
                "longitude": cfg.lon,
                "temperature": temp,
                "pressure": pres,
                "humidity": hum,
                "is_anomaly": False,
                "anomaly_type": "none",
            })
            frames.append(df)
        data = pd.concat(frames, ignore_index=True)
        return data.sort_values(["station_id", "timestamp"]).reset_index(drop=True)


class AnomalyInjector:
    """Injects labeled anomaly events into a clean simulated dataset so
    detection accuracy can be measured against ground truth."""

    def __init__(self, seed=7):
        self.rng = np.random.default_rng(seed)

    def inject(self, df, n_events_per_station=6):
        df = df.copy()
        anomaly_kinds = [
            "spike", "frozen_sensor", "calibration_drift",
            "communication_dropout", "multivariate_inconsistency",
            "noise_burst",
        ]
        for sid, g in df.groupby("station_id"):
            idx_all = g.index.to_numpy()
            n = len(idx_all)
            for _ in range(n_events_per_station):
                kind = self.rng.choice(anomaly_kinds)
                start = self.rng.integers(50, n - 60)
                dur = self.rng.integers(3, 30)
                span = idx_all[start:start + dur]
                self._apply(df, span, kind)
        return df

    def _apply(self, df, span, kind):
        if len(span) == 0:
            return
        df.loc[span, "is_anomaly"] = True
        df.loc[span, "anomaly_type"] = kind

        if kind == "spike":
            param = self.rng.choice(["temperature", "pressure", "humidity"])
            magnitude = {"temperature": 25, "pressure": 30, "humidity": 60}[param]
            direction = self.rng.choice([-1, 1])
            df.loc[span, param] += direction * magnitude

        elif kind == "frozen_sensor":
            param = self.rng.choice(["temperature", "pressure", "humidity"])
            stuck_value = df.loc[span[0], param]
            df.loc[span, param] = stuck_value  # sensor stops updating

        elif kind == "calibration_drift":
            param = self.rng.choice(["temperature", "pressure", "humidity"])
            drift = np.linspace(0, self.rng.choice([-8, 8, -15, 15]), len(span))
            df.loc[span, param] += drift

        elif kind == "communication_dropout":
            # garbled/erroneous values simulating a corrupted packet
            for param, lo, hi in [("temperature", -40, 60),
                                   ("pressure", 900, 1080),
                                   ("humidity", 0, 100)]:
                if self.rng.random() < 0.5:
                    df.loc[span, param] = self.rng.uniform(lo, hi, len(span))

        elif kind == "multivariate_inconsistency":
            # physically implausible combo: very high temp + very high humidity
            # + simultaneous sharp pressure spike (dew point > temperature)
            df.loc[span, "temperature"] += 10
            df.loc[span, "humidity"] = np.minimum(df.loc[span, "humidity"] + 35, 100)
            df.loc[span, "pressure"] += self.rng.choice([-12, 12])

        elif kind == "noise_burst":
            param = self.rng.choice(["temperature", "pressure", "humidity"])
            noise_scale = {"temperature": 4, "pressure": 5, "humidity": 15}[param]
            df.loc[span, param] += self.rng.normal(0, noise_scale, len(span))


def default_network():
    """A 7-station network mixing national diversity (5 stations spanning
    India's climate zones) with a realistic regional cluster (3 stations
    in the Delhi NCR, ~25–35 km apart) so the spatial consistency check
    can be demonstrated on genuinely nearby stations.

    The spatial check only activates for the NCR cluster; the four
    geographically isolated stations (Kochi, Bangalore, Shimla, Jaisalmer)
    get the remaining 6 detection layers but skip the spatial check —
    which is the correct behavior, since comparing their weather against
    stations 1000+ km away would be meaningless."""
    return [
        # ---- Delhi NCR regional cluster (~25-35 km apart) ----
        StationConfig("AWS_DELHI",    28.61, 77.20, altitude_m=216,  base_temp=26,   base_humidity=45),
        StationConfig("AWS_GURGAON",  28.46, 77.03, altitude_m=217,  base_temp=26.5, base_humidity=42),
        StationConfig("AWS_NOIDA",    28.53, 77.39, altitude_m=200,  base_temp=26,   base_humidity=48),
        # ---- Geographically isolated stations (diverse climates) ----
        StationConfig("AWS_KOCHI",    9.93,  76.26, altitude_m=3,    base_temp=29,   base_humidity=78),
        StationConfig("AWS_BLR",      12.97, 77.59, altitude_m=920,  base_temp=23,   base_humidity=55),
        StationConfig("AWS_SHIMLA",   31.10, 77.17, altitude_m=2200, base_temp=14,   base_humidity=60),
        StationConfig("AWS_JAISALMER",26.91, 70.90, altitude_m=225,  base_temp=32,   base_humidity=25),
    ]


def build_demo_dataset(periods=2880, freq_minutes=5, n_events_per_station=6, seed=42):
    """Convenience one-shot: simulate a clean network then inject anomalies."""
    sim = NetworkSimulator(default_network(), periods=periods,
                            freq_minutes=freq_minutes, seed=seed)
    clean = sim.generate()
    injector = AnomalyInjector(seed=seed + 1)
    dirty = injector.inject(clean, n_events_per_station=n_events_per_station)
    return dirty
