# spreads.py — Spread calculations
#
# Responsibilities:
#   - Load raw CSVs from data/raw/
#   - Calculate spreads between RIC pairs (pairs to be defined in config.py)
#   - Save one CSV per spread to data/processed/{spread_name}.csv
#
# Expected input:  data/raw/{RIC}.csv
# Expected output: data/processed/{RIC_A}_minus_{RIC_B}.csv

def calculate_spreads():
    raise NotImplementedError
