"""Fuel CSV loading, deduplication, and geocode cache utilities."""

import csv
import json
import logging
from pathlib import Path
from typing import TypedDict

import pygeohash as pgh
from geopy.geocoders import ArcGIS
from django.conf import settings

logger = logging.getLogger(__name__)

_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}


class FuelStop(TypedDict):
    stop_id: int
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lon: float


def _load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return {}


def _save_cache(cache_path: Path, cache: dict) -> None:
    with open(cache_path, "w") as f:
        json.dump(cache, f)


def _parse_csv(csv_path: Path) -> dict[int, dict]:
    """Parse CSV and deduplicate per stop_id, keeping minimum retail price."""
    best: dict[int, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            state = row["State"].strip().upper()
            if state not in _US_STATE_CODES:
                continue
            try:
                stop_id = int(row["OPIS Truckstop ID"])
                price = float(row["Retail Price"])
            except (ValueError, KeyError):
                continue
            if stop_id not in best or price < best[stop_id]["price"]:
                best[stop_id] = {
                    "stop_id": stop_id,
                    "name": row["Truckstop Name"].strip(),
                    "address": row["Address"].strip(),
                    "city": row["City"].strip(),
                    "state": state,
                    "price": price,
                }
    return best


def geocode_missing(
    cache_path: Path,
    csv_path: Path,
    progress_callback=None,
) -> tuple[int, int]:
    """Geocode city/state pairs missing from cache."""
    cache = _load_cache(cache_path)
    stops = _parse_csv(csv_path)

    city_state_pairs = {(s["city"], s["state"]) for s in stops.values()}
    missing = [(c, st) for c, st in city_state_pairs if f"{c},{st}" not in cache]

    if not missing:
        return 0, len(city_state_pairs)

    geolocator = ArcGIS(timeout=10)

    newly_done = 0
    for city, state in missing:
        query = f"{city}, {state}, USA"
        try:
            location = geolocator.geocode(query, exactly_one=True)
        except Exception as exc:
            logger.warning("Geocoding failed for %s, %s: %s", city, state, exc)
            location = None
        key = f"{city},{state}"
        if location:
            cache[key] = [location.latitude, location.longitude]
        else:
            cache[key] = []
            logger.debug("No result for %s, %s", city, state)
        newly_done += 1
        if progress_callback:
            progress_callback(newly_done, len(missing), city, state)

    _save_cache(cache_path, cache)
    logger.info("Geocoding complete: %d new, %d total pairs cached", newly_done, len(cache))
    return newly_done, len(city_state_pairs) - len(missing)


_GEOHASH_PRECISION = 4  # cells ≈ 40 × 20 km; sufficient for 5-mile corridor lookup


def build_geohash_index(
    stops: list[FuelStop],
    precision: int = _GEOHASH_PRECISION,
) -> dict[str, list[FuelStop]]:
    """Build `geohash -> stops` index for nearby stop lookup."""
    index: dict[str, list[FuelStop]] = {}
    for stop in stops:
        cell = pgh.encode(stop["lat"], stop["lon"], precision=precision)
        index.setdefault(cell, []).append(stop)
    return index


def load_fuel_stops() -> tuple[list[FuelStop], dict[str, list[FuelStop]]]:
    """Load fuel stops and return `(stops, geohash_index)`."""
    csv_path: Path = settings.FUEL_CSV_PATH
    cache_path: Path = settings.FUEL_GEOCODE_CACHE_PATH

    cache = _load_cache(cache_path)
    best = _parse_csv(csv_path)

    stops: list[FuelStop] = []
    missing_count = 0
    for row in best.values():
        key = f"{row['city']},{row['state']}"
        coords = cache.get(key)
        if coords is None:
            missing_count += 1
            continue
        if len(coords) != 2:
            continue
        stops.append(
            FuelStop(
                stop_id=row["stop_id"],
                name=row["name"],
                address=row["address"],
                city=row["city"],
                state=row["state"],
                price=row["price"],
                lat=coords[0],
                lon=coords[1],
            )
        )

    if missing_count:
        logger.warning(
            "%d fuel stops have no cached coordinates. "
            "Run: python manage.py build_fuel_cache",
            missing_count,
        )
    logger.info("Loaded %d fuel stops with coordinates", len(stops))
    return stops, build_geohash_index(stops)
