"""Geocoding and routing clients backed by Nominatim and ORS."""

import concurrent.futures
from functools import lru_cache

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {"User-Agent": "spotter-assessment/1.0"}
_ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"

_session = requests.Session()
_retry = Retry(
    total=2,
    backoff_factor=0.3,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=("GET", "POST"),
)
_adapter = HTTPAdapter(
    pool_connections=10, pool_maxsize=10, max_retries=_retry
)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


@lru_cache(maxsize=512)
def geocode(location: str) -> tuple[float, float]:
    """Return `(lat, lon)` for a US location string."""
    resp = _session.get(
        _NOMINATIM_URL,
        params={"q": location, "countrycodes": "us", "format": "json", "limit": 1},
        headers=_NOMINATIM_HEADERS,
        timeout=5,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode location: {location!r}")
    hit = results[0]
    return float(hit["lat"]), float(hit["lon"])


def geocode_pair(
    start: str, finish: str
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Geocode start and finish concurrently."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_start = pool.submit(geocode, start)
        f_finish = pool.submit(geocode, finish)
        start_coords = f_start.result()
        finish_coords = f_finish.result()
    return start_coords, finish_coords


def get_route(
    start_lat: float, start_lon: float, finish_lat: float, finish_lon: float
) -> dict:
    """Call ORS and return route geometry, distance, and duration."""
    api_key = settings.ORS_API_KEY
    if not api_key:
        raise RuntimeError("ORS_API_KEY is not configured. Add it to .env.")

    payload = {
        "coordinates": [[start_lon, start_lat], [finish_lon, finish_lat]],
        "instructions": False,
        "geometry_simplify": True,
    }
    resp = _session.post(
        _ORS_URL,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    features = data.get("features") or []
    if not features:
        raise ValueError("ORS returned no route features.")

    feat = features[0]
    summary = feat["properties"]["summary"]
    return {
        "geometry": feat["geometry"]["coordinates"],
        "distance_meters": summary["distance"],
        "duration_seconds": summary["duration"],
    }
