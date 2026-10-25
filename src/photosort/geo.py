"""Where on earth a place is: the country and city nearest a GPS position.

The lookup is offline: reverse_geocode's bundled city list (GeoNames cities
of 1000+ people) and a KD-tree over it. reverse_geocode (and the 3 MB city
list, about 0.7 s to load) is imported on the first lookup, so the app and
the other modes never load it.
"""

import functools
import logging
import math
from pathlib import Path
from types import ModuleType

from photosort.thresholds import GEO_MAX_CITY_KM

_log = logging.getLogger("photosort")
_EARTH_RADIUS_KM = 6371.0088  # the mean radius

# reverse_geocode downloads its data when these bundled files are missing;
# then no lookup is made at all (no photo data over the network, ever).
_DATA_FILES = ("geocode.gz", "countries.csv")


def place_of(lat: float, lon: float) -> tuple[str, str] | None:
    """(country, city) of the city nearest (lat, lon), city "" if unnamed.

    None if the lookup fails, gives no country, or finds no city within
    GEO_MAX_CITY_KM (a photo taken at sea or at a pole is in no city's country).
    """
    geocoder = _geocoder()
    if geocoder is None:
        return None
    try:
        found = geocoder.get((lat, lon))
        country = (found.get("country") or "").strip()
        city = (found.get("city") or "").strip()
        away = _km_between(lat, lon, float(found["latitude"]), float(found["longitude"]))
    except Exception:
        return None
    if not country or away > GEO_MAX_CITY_KM:
        return None
    return country, city


def _km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """The great-circle distance between two positions (haversine formula)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


@functools.cache
def _geocoder() -> ModuleType | None:
    """reverse_geocode with its city list loaded, or None if it can't be
    (logged, and not tried again)."""
    try:
        import reverse_geocode

        here = Path(reverse_geocode.__file__).parent
        missing = [name for name in _DATA_FILES if not (here / name).is_file()]
        if missing:
            _log.warning("Location lookup off: %s missing from %s", ", ".join(missing), here)
            return None
        reverse_geocode.get((0.0, 0.0))  # loads the list and builds the tree
    except Exception as e:
        _log.warning("Location lookup off: can't load the city list: %s",
                     str(e) or type(e).__name__)
        return None
    return reverse_geocode
