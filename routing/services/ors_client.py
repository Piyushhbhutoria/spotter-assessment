"""
OpenRouteService routing client + Nominatim geocoding.

External calls per route request:
  1. Nominatim geocode start location
  2. Nominatim geocode finish location
  3. ORS directions (single call, returns full GeoJSON geometry)
"""

import requests
from django.conf import settings

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
_NOMINATIM_HEADERS = {"User-Agent": "spotter-assessment/1.0"}


def geocode(location: str) -> tuple[float, float]:
    """Return (lat, lon) for a US location string using Nominatim."""
    resp = requests.get(
        _NOMINATIM_URL,
        params={"q": location, "countrycodes": "us", "format": "json", "limit": 1},
        headers=_NOMINATIM_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode location: {location!r}")
    hit = results[0]
    return float(hit["lat"]), float(hit["lon"])


def get_route(
    start_lat: float, start_lon: float, finish_lat: float, finish_lon: float
) -> dict:
    """
    Call ORS directions and return a dict with:
      - geometry: GeoJSON LineString coordinates list [[lon, lat], ...]
      - distance_meters: total route distance
      - duration_seconds: total route duration
    """
    if not settings.ORS_API_KEY:
        raise RuntimeError(
            "ORS_API_KEY is not configured. Set it in the .env file."
        )

    payload = {
        "coordinates": [
            [start_lon, start_lat],
            [finish_lon, finish_lat],
        ]
    }
    resp = requests.post(
        _ORS_URL,
        json=payload,
        headers={
            "Authorization": settings.ORS_API_KEY,
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    feature = data["features"][0]
    summary = feature["properties"]["summary"]
    coords = feature["geometry"]["coordinates"]

    return {
        "geometry": coords,
        "distance_meters": summary["distance"],
        "duration_seconds": summary["duration"],
    }
