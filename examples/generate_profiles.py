"""Print one weekday + one weekend hourly profile for every built-in building type.

Run: python examples/generate_profiles.py
"""
from datetime import datetime

from gridweave.simulation import DEFAULT_PROFILES, DemandGenerator

MONDAY, SATURDAY = datetime(2026, 1, 5), datetime(2026, 1, 10)
BLOCKS = " ▁▂▃▄▅▆▇█"

print("Hourly mean demand for a 100 kW building (seed 1). Sparkline: 00h ... 23h\n")
for name, profile in DEFAULT_PROFILES.items():
    gen = DemandGenerator(profile, capacity_kw=100, seed=1)
    for label, day in (("weekday", MONDAY), ("weekend", SATURDAY)):
        samples = gen.generate(day, 96)
        hourly = [sum(s.demand_kw for s in samples[h * 4:h * 4 + 4]) / 4 for h in range(24)]
        spark = "".join(BLOCKS[min(8, int(v / 100 * 8.999))] for v in hourly)
        print(f"{name:<9}{label:<8} {spark}  peak {max(hourly):5.1f} kW @ {hourly.index(max(hourly)):02d}h, "
              f"min {min(hourly):5.1f} kW")
