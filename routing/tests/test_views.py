"""Tests for the /api/route/ endpoint."""

import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


_MOCK_ROUTE = {
    "geometry": [
        [-87.63, 41.85],
        [-85.0, 41.70],
        [-83.04, 41.50],
        [-79.99, 40.44],
    ],
    "distance_meters": 950_000,
    "duration_seconds": 32400,
}

_MOCK_STOPS = [
    {
        "name": "Pilot #1",
        "address": "I-80 EXIT 100",
        "city": "Gary",
        "state": "IN",
        "lat": 41.60,
        "lon": -87.33,
        "price_per_gallon": 3.10,
        "gallons": 30.0,
        "cost": 93.0,
        "position_miles": 300.0,
    }
]

_MOCK_COORDS = ((41.85, -87.63), (34.05, -118.24))


class TestRouteView(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = "/api/route/"

    def _post(self, body: dict) -> MagicMock:
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_missing_start_returns_400(self):
        resp = self._post({"finish": "Los Angeles, CA"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_missing_finish_returns_400(self):
        resp = self._post({"start": "Chicago, IL"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    @patch("routing.views._get_fuel_data")
    @patch("routing.views.ors_client.get_route")
    @patch("routing.views.ors_client.geocode_pair")
    def test_empty_fuel_cache_returns_503(self, mock_pair, mock_route, mock_data):
        mock_pair.return_value = _MOCK_COORDS
        mock_route.return_value = _MOCK_ROUTE
        mock_data.return_value = ([], {})
        resp = self._post({"start": "Chicago, IL", "finish": "Los Angeles, CA"})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("build_fuel_cache", resp.json()["error"])

    def test_unknown_location_returns_400(self):
        with patch("routing.views.ors_client.geocode_pair") as mock_pair:
            mock_pair.side_effect = ValueError("Could not geocode location: 'Xyz123'")
            resp = self._post({"start": "Xyz123", "finish": "Los Angeles, CA"})
        self.assertEqual(resp.status_code, 400)

    @patch("routing.views._get_fuel_data")
    @patch("routing.views.ors_client.get_route")
    @patch("routing.views.ors_client.geocode_pair")
    def test_successful_route_response_shape(self, mock_pair, mock_route, mock_data):
        mock_pair.return_value = _MOCK_COORDS
        mock_route.return_value = _MOCK_ROUTE
        mock_data.return_value = (_MOCK_STOPS, {"dp3w": _MOCK_STOPS})

        with patch("routing.views.optimizer.select_fuel_stops") as mock_opt:
            mock_opt.return_value = (_MOCK_STOPS, 93.0)
            resp = self._post({"start": "Chicago, IL", "finish": "Los Angeles, CA"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("start", data)
        self.assertIn("finish", data)
        self.assertIn("total_distance_miles", data)
        self.assertIn("total_fuel_cost", data)
        self.assertIn("route_geometry", data)
        self.assertIn("fuel_stops", data)
        self.assertEqual(data["route_geometry"]["type"], "LineString")
        self.assertEqual(len(data["fuel_stops"]), 1)
        self.assertEqual(data["total_fuel_cost"], 93.0)

    @patch("routing.views._get_fuel_data")
    @patch("routing.views.ors_client.get_route")
    @patch("routing.views.ors_client.geocode_pair")
    def test_no_feasible_route_returns_422(self, mock_pair, mock_route, mock_data):
        mock_pair.return_value = _MOCK_COORDS
        mock_route.return_value = _MOCK_ROUTE
        mock_data.return_value = (_MOCK_STOPS, {"dp3w": _MOCK_STOPS})

        with patch("routing.views.optimizer.select_fuel_stops") as mock_opt:
            mock_opt.side_effect = ValueError("No feasible route found")
            resp = self._post({"start": "Chicago, IL", "finish": "Los Angeles, CA"})

        self.assertEqual(resp.status_code, 422)
