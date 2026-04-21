"""Tests for the fuel-stop optimizer."""

import math

from django.test import TestCase, override_settings

from routing.services.optimizer import (
    select_fuel_stops,
    _haversine_miles,
    _cumulative_distances_miles,
    _candidate_stops,
    METERS_PER_MILE,
)
from routing.services.fuel_data import FuelStop, build_geohash_index


def _make_stop(
    stop_id: int,
    name: str,
    lat: float,
    lon: float,
    price: float,
) -> FuelStop:
    return FuelStop(
        stop_id=stop_id,
        name=name,
        address="",
        city="City",
        state="TX",
        price=price,
        lat=lat,
        lon=lon,
    )


def _interpolate_route(
    start: list[float], end: list[float], n_points: int = 50
) -> list[list[float]]:
    """Generate n_points linearly interpolated between start and end [lon, lat]."""
    return [
        [
            start[0] + (end[0] - start[0]) * i / (n_points - 1),
            start[1] + (end[1] - start[1]) * i / (n_points - 1),
        ]
        for i in range(n_points)
    ]


_LONG_ROUTE = _interpolate_route([-87.63, 41.85], [-74.01, 40.71], n_points=100)

_SHORT_ROUTE = _interpolate_route([-87.63, 41.85], [-81.69, 41.50], n_points=50)


@override_settings(ROUTE_CORRIDOR_MILES=20)
class TestHaversine(TestCase):
    def test_zero_distance(self):
        self.assertAlmostEqual(_haversine_miles(41.0, -87.0, 41.0, -87.0), 0.0)

    def test_chicago_to_cleveland(self):
        dist = _haversine_miles(41.85, -87.63, 41.50, -83.04)
        self.assertGreater(dist, 200)
        self.assertLess(dist, 270)

    def test_symmetry(self):
        d1 = _haversine_miles(41.85, -87.63, 40.71, -74.01)
        d2 = _haversine_miles(40.71, -74.01, 41.85, -87.63)
        self.assertAlmostEqual(d1, d2, places=6)


@override_settings(ROUTE_CORRIDOR_MILES=20)
class TestCumulativeDistances(TestCase):
    def test_starts_at_zero(self):
        cum = _cumulative_distances_miles(_LONG_ROUTE)
        self.assertEqual(cum[0], 0.0)

    def test_monotonically_increasing(self):
        cum = _cumulative_distances_miles(_LONG_ROUTE)
        for i in range(1, len(cum)):
            self.assertGreater(cum[i], cum[i - 1])

    def test_chicago_to_nyc_roughly_700_to_800(self):
        cum = _cumulative_distances_miles(_LONG_ROUTE)
        self.assertGreater(cum[-1], 700)
        self.assertLess(cum[-1], 850)


@override_settings(ROUTE_CORRIDOR_MILES=20)
class TestCandidateStops(TestCase):
    """Verify the geohash pre-filter returns on-route stops and omits distant ones."""

    def test_on_route_stop_is_a_candidate(self):
        mid = _LONG_ROUTE[len(_LONG_ROUTE) // 2]
        on_route = _make_stop(1, "On Route", mid[1], mid[0], 3.00)
        index = build_geohash_index([on_route])
        cum = _cumulative_distances_miles(_LONG_ROUTE)
        candidates = _candidate_stops(_LONG_ROUTE, cum, index)
        self.assertIn(on_route, candidates)

    def test_far_off_route_stop_is_not_a_candidate(self):
        mid = _LONG_ROUTE[len(_LONG_ROUTE) // 2]
        far_stop = _make_stop(2, "Off Route", 25.0, mid[0], 1.00)
        index = build_geohash_index([far_stop])
        cum = _cumulative_distances_miles(_LONG_ROUTE)
        candidates = _candidate_stops(_LONG_ROUTE, cum, index)
        self.assertNotIn(far_stop, candidates)

    def test_candidate_set_is_smaller_than_total(self):
        """With stops scattered across the US, only route-adjacent ones are returned."""
        route_mid = _LONG_ROUTE[len(_LONG_ROUTE) // 2]
        on_route = _make_stop(1, "On Route", route_mid[1], route_mid[0], 3.00)
        off_route_stops = [
            _make_stop(i + 2, f"Off {i}", 25.0 + i, -100.0 + i * 5, 3.00)
            for i in range(20)
        ]
        all_stops = [on_route] + off_route_stops
        index = build_geohash_index(all_stops)
        cum = _cumulative_distances_miles(_LONG_ROUTE)
        candidates = _candidate_stops(_LONG_ROUTE, cum, index)
        self.assertLess(len(candidates), len(all_stops))
        self.assertIn(on_route, candidates)


@override_settings(ROUTE_CORRIDOR_MILES=20)
class TestSelectFuelStops(TestCase):
    """Use a ~790-mile route that requires at least one fuel stop."""

    def _long_route_meters(self) -> float:
        cum = _cumulative_distances_miles(_LONG_ROUTE)
        return cum[-1] * METERS_PER_MILE

    def _short_route_meters(self) -> float:
        cum = _cumulative_distances_miles(_SHORT_ROUTE)
        return cum[-1] * METERS_PER_MILE

    def _midpoint_stop(self, name: str, price: float, stop_id: int = 1) -> FuelStop:
        mid = _LONG_ROUTE[len(_LONG_ROUTE) // 2]
        return _make_stop(stop_id, name, mid[1], mid[0], price)

    def test_selects_cheapest_stop_in_range(self):
        mid_idx = len(_LONG_ROUTE) // 2
        pt_cheap = _LONG_ROUTE[mid_idx]
        pt_exp = _LONG_ROUTE[mid_idx + 2]

        cheap = _make_stop(1, "Cheap Stop", pt_cheap[1], pt_cheap[0], 2.80)
        expensive = _make_stop(2, "Expensive Stop", pt_exp[1], pt_exp[0], 3.50)
        index = build_geohash_index([cheap, expensive])

        stops, cost = select_fuel_stops(
            _LONG_ROUTE, self._long_route_meters(), [cheap, expensive],
            geohash_index=index,
        )
        names = {s["name"] for s in stops}
        self.assertIn("Cheap Stop", names)
        self.assertGreater(cost, 0)

    def test_short_route_needs_no_stop(self):
        stops, cost = select_fuel_stops(_SHORT_ROUTE, self._short_route_meters(), [])
        self.assertEqual(stops, [])
        self.assertEqual(cost, 0.0)

    def test_long_route_no_stops_raises(self):
        with self.assertRaises(ValueError):
            select_fuel_stops(_LONG_ROUTE, self._long_route_meters(), [])

    def test_cost_calculation(self):
        stop = self._midpoint_stop("Middle Stop", 3.00)
        index = build_geohash_index([stop])
        stops_result, total_cost = select_fuel_stops(
            _LONG_ROUTE, self._long_route_meters(), [stop],
            geohash_index=index,
        )
        self.assertGreater(len(stops_result), 0)
        for s in stops_result:
            expected_cost = round(s["gallons"] * s["price_per_gallon"], 2)
            self.assertAlmostEqual(s["cost"], expected_cost, places=2)
        self.assertAlmostEqual(total_cost, sum(s["cost"] for s in stops_result), places=2)

    def test_stop_outside_corridor_ignored(self):
        mid = _LONG_ROUTE[len(_LONG_ROUTE) // 2]
        far_stop = _make_stop(1, "Off Route", 30.0, mid[0], 1.00)
        near_stop = _make_stop(2, "On Route", mid[1], mid[0], 3.00)
        index = build_geohash_index([far_stop, near_stop])

        stops_result, _ = select_fuel_stops(
            _LONG_ROUTE, self._long_route_meters(),
            [far_stop, near_stop], geohash_index=index,
        )
        names = {s["name"] for s in stops_result}
        self.assertNotIn("Off Route", names)
        self.assertIn("On Route", names)

    def test_works_without_geohash_index(self):
        """geohash_index=None falls back to full scan; result should be identical."""
        stop = self._midpoint_stop("Stop", 3.00)
        index = build_geohash_index([stop])

        stops_with, cost_with = select_fuel_stops(
            _LONG_ROUTE, self._long_route_meters(), [stop], geohash_index=index
        )
        stops_without, cost_without = select_fuel_stops(
            _LONG_ROUTE, self._long_route_meters(), [stop], geohash_index=None
        )
        self.assertEqual(cost_with, cost_without)
        self.assertEqual(len(stops_with), len(stops_without))
