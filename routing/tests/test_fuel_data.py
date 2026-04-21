"""Tests for fuel data loading, deduplication, and geohash indexing."""

import csv
import json
import os
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from routing.services.fuel_data import (
    _load_cache,
    _save_cache,
    _US_STATE_CODES,
    build_geohash_index,
    load_fuel_stops,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "OPIS Truckstop ID",
        "Truckstop Name",
        "Address",
        "City",
        "State",
        "Rack ID",
        "Retail Price",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class TestUsCodes(TestCase):
    def test_contains_all_50_states_and_dc(self):
        self.assertEqual(len(_US_STATE_CODES), 51)

    def test_excludes_canadian_provinces(self):
        self.assertNotIn("QC", _US_STATE_CODES)
        self.assertNotIn("ON", _US_STATE_CODES)


class TestCache(TestCase):
    def test_load_missing_cache_returns_empty(self):
        result = _load_cache(Path("/nonexistent/path.json"))
        self.assertEqual(result, {})

    def test_save_and_reload(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            data = {"Chicago,IL": [41.85, -87.65], "Austin,TX": [30.27, -97.74]}
            _save_cache(path, data)
            loaded = _load_cache(path)
            self.assertEqual(loaded, data)
        finally:
            os.unlink(path)


class TestLoadFuelStops(TestCase):
    """Integration test with a tiny synthetic CSV + pre-built geocode cache."""

    def test_returns_tuple_of_stops_and_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "fuel.csv"
            cache_path = Path(tmpdir) / "cache.json"
            _write_csv(csv_path, [{
                "OPIS Truckstop ID": "1", "Truckstop Name": "Stop",
                "Address": "I-80", "City": "Chicago", "State": "IL",
                "Rack ID": "1", "Retail Price": "3.00",
            }])
            _save_cache(cache_path, {"Chicago,IL": [41.85, -87.65]})

            with override_settings(FUEL_CSV_PATH=csv_path, FUEL_GEOCODE_CACHE_PATH=cache_path):
                result = load_fuel_stops()

            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 2)
            stops, index = result
            self.assertIsInstance(stops, list)
            self.assertIsInstance(index, dict)

    def test_deduplicates_same_stop_id_keeps_min_price(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "fuel.csv"
            cache_path = Path(tmpdir) / "cache.json"

            _write_csv(csv_path, [
                {"OPIS Truckstop ID": "7", "Truckstop Name": "Stop A",
                 "Address": "I-44", "City": "Chicago", "State": "IL",
                 "Rack ID": "1", "Retail Price": "3.50"},
                {"OPIS Truckstop ID": "7", "Truckstop Name": "Stop A",
                 "Address": "I-44", "City": "Chicago", "State": "IL",
                 "Rack ID": "1", "Retail Price": "3.20"},
            ])
            _save_cache(cache_path, {"Chicago,IL": [41.85, -87.65]})

            with override_settings(FUEL_CSV_PATH=csv_path, FUEL_GEOCODE_CACHE_PATH=cache_path):
                stops, _ = load_fuel_stops()

            self.assertEqual(len(stops), 1)
            self.assertAlmostEqual(stops[0]["price"], 3.20)

    def test_filters_non_us_stops(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "fuel.csv"
            cache_path = Path(tmpdir) / "cache.json"

            _write_csv(csv_path, [
                {"OPIS Truckstop ID": "1", "Truckstop Name": "US Stop",
                 "Address": "I-80", "City": "Chicago", "State": "IL",
                 "Rack ID": "1", "Retail Price": "3.00"},
                {"OPIS Truckstop ID": "2", "Truckstop Name": "CA Stop",
                 "Address": "Hwy-401", "City": "Toronto", "State": "ON",
                 "Rack ID": "2", "Retail Price": "3.00"},
            ])
            _save_cache(cache_path, {"Chicago,IL": [41.85, -87.65]})

            with override_settings(FUEL_CSV_PATH=csv_path, FUEL_GEOCODE_CACHE_PATH=cache_path):
                stops, _ = load_fuel_stops()

            self.assertEqual(len(stops), 1)
            self.assertEqual(stops[0]["city"], "Chicago")


class TestBuildGeohashIndex(TestCase):
    def _make_stop(self, stop_id, lat, lon):
        from routing.services.fuel_data import FuelStop
        return FuelStop(stop_id=stop_id, name="S", address="",
                        city="C", state="TX", price=3.0, lat=lat, lon=lon)

    def test_index_contains_all_stops(self):
        stops = [
            self._make_stop(1, 41.85, -87.63),
            self._make_stop(2, 34.05, -118.24),
        ]
        index = build_geohash_index(stops)
        all_indexed = [s for cell_stops in index.values() for s in cell_stops]
        self.assertEqual(len(all_indexed), 2)

    def test_stops_in_same_area_share_cell(self):
        """Two stops < 1 km apart should share a precision-4 cell."""
        stops = [
            self._make_stop(1, 41.850, -87.630),
            self._make_stop(2, 41.851, -87.631),
        ]
        index = build_geohash_index(stops, precision=4)
        self.assertEqual(len(index), 1)

    def test_distant_stops_in_different_cells(self):
        """Stops 2000 miles apart must not share a cell."""
        stops = [
            self._make_stop(1, 41.85, -87.63),
            self._make_stop(2, 34.05, -118.24),
        ]
        index = build_geohash_index(stops, precision=4)
        self.assertEqual(len(index), 2)
