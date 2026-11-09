"""Reference places and distance helpers.

!! Coordinates are APPROXIMATE centre points for screening only. Verify each one
!! against a chart or published source and cite it in SOURCES.md before relying on it.
"""

from __future__ import annotations

import numpy as np

# Known ship-to-ship (STS) transfer areas linked to Russian oil in public reporting.
STS_HOTSPOTS = {
    "Laconian Gulf (GR)": (36.55, 22.70),
    "Messinian Gulf / Kalamata (GR)": (36.90, 22.00),
    "Off Ceuta (Strait of Gibraltar)": (35.90, -5.25),
    "Eastern OPL off Johor (MY)": (1.30, 104.55),
    "Off Lomé (TG)": (6.00, 1.30),
}

# Russian crude / product export terminals.
RUSSIAN_TERMINALS = {
    "Primorsk": (60.35, 28.67),
    "Ust-Luga": (59.68, 28.40),
    "Novorossiysk (Sheskharis)": (44.68, 37.79),
    "Tuapse": (44.09, 39.07),
    "Murmansk": (68.97, 33.08),
    "Kozmino": (42.73, 133.08),
    "De-Kastri": (51.47, 140.78),
}

EARTH_R_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km. Works on scalars or numpy/pandas arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


def nearest(lat, lon, places: dict[str, tuple[float, float]]) -> tuple[str | None, float]:
    """Name and distance (km) of the closest place, or (None, inf) if lat/lon missing."""
    if lat is None or lon is None or np.isnan(lat) or np.isnan(lon):
        return None, float("inf")
    best, best_d = None, float("inf")
    for name, (plat, plon) in places.items():
        d = float(haversine_km(lat, lon, plat, plon))
        if d < best_d:
            best, best_d = name, d
    return best, best_d
