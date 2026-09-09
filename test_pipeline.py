import sys
import logging
logging.basicConfig(level=logging.DEBUG)

from skyguard.real_data_loader import fetch_open_meteo_data
from skyguard.pipeline import SkyGuardPipeline
from skyguard.correction import apply_corrections

print("Fetching data...")
df = fetch_open_meteo_data(past_days=10)
print("Data fetched. Running pipeline...")
results, health = SkyGuardPipeline(contamination=0.05).run(df, apply_correction=True)
print("Pipeline complete!")
