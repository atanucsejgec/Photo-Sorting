"""Where on earth a place is: the country and city nearest a GPS position.

The lookup is offline: reverse_geocode's bundled city list (GeoNames cities
of 1000+ people) and a KD-tree over it. reverse_geocode (and the 3 MB city
list, about 0.7 s to load) is imported on the first lookup, so the app and
the other modes never load it.
"""

import functools
from pathlib import Path
from types import ModuleType

# reverse_geocode downloads its data when these bundled files are missing;
# then no lookup is made at all (no photo data over the network, ever).
_DATA_FILES = ("geocode.gz", "countries.csv")


def place_of(lat: float, lon: float) -> tuple[str, str] | None:
    """(country, city) of the city nearest (lat, lon), city "" if unnamed.

    None if the lookup fails or gives no country.
    """
    geocoder = _geocoder()
    if geocoder is None:
        return None
    try:
        found = geocoder.get((lat, lon))
        country = (found.get("country") or "").strip()
        city = (found.get("city") or "").strip()
    except Exception:
        return None
    if not country:
        return None
    return country, city


@functools.cache
def _geocoder() -> ModuleType | None:
    """reverse_geocode with its city list loaded, or None if it can't be
    (and it is not tried again)."""
    try:
        import reverse_geocode

        here = Path(reverse_geocode.__file__).parent
        if not all((here / name).is_file() for name in _DATA_FILES):
            return None
        reverse_geocode.get((0.0, 0.0))  # loads the list and builds the tree
    except Exception:
        return None
    return reverse_geocode
