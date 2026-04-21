"""
POST /api/route/

Request body:
    { "start": "Chicago, IL", "finish": "Los Angeles, CA" }

Response:
    {
        "start": "Chicago, IL",
        "finish": "Los Angeles, CA",
        "total_distance_miles": 2015.4,
        "total_fuel_cost": 614.22,
        "route_geometry": { "type": "LineString", "coordinates": [[lon, lat], ...] },
        "fuel_stops": [
            {
                "name": "Pilot Travel Center #123",
                "address": "I-80, EXIT 211 & US-30",
                "city": "Cheyenne",
                "state": "WY",
                "lat": 41.14,
                "lon": -104.82,
                "price_per_gallon": 3.207,
                "gallons": 40.0,
                "cost": 128.28,
                "position_miles": 400.0
            }
        ]
    }
"""

import logging

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
import json

from .services import ors_client, fuel_data, optimizer

logger = logging.getLogger(__name__)

_METERS_PER_MILE = 1609.344

# Fuel stops loaded once at module import time (warm cache reused across requests)
_fuel_stops: list | None = None


def _get_fuel_stops():
    global _fuel_stops
    if _fuel_stops is None:
        _fuel_stops = fuel_data.load_fuel_stops()
    return _fuel_stops


@method_decorator(csrf_exempt, name="dispatch")
class RouteView(View):
    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        start = (body.get("start") or "").strip()
        finish = (body.get("finish") or "").strip()

        if not start or not finish:
            return JsonResponse(
                {"error": "Both 'start' and 'finish' fields are required."}, status=400
            )

        try:
            start_lat, start_lon = ors_client.geocode(start)
            finish_lat, finish_lon = ors_client.geocode(finish)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Geocoding failed")
            return JsonResponse({"error": f"Geocoding error: {exc}"}, status=502)

        try:
            route = ors_client.get_route(start_lat, start_lon, finish_lat, finish_lon)
        except RuntimeError as exc:
            return JsonResponse({"error": str(exc)}, status=500)
        except Exception as exc:
            logger.exception("Routing failed")
            return JsonResponse({"error": f"Routing error: {exc}"}, status=502)

        try:
            stops = _get_fuel_stops()
        except Exception as exc:
            logger.exception("Failed to load fuel stops")
            return JsonResponse({"error": f"Fuel data error: {exc}"}, status=500)

        if not stops:
            return JsonResponse(
                {
                    "error": (
                        "Fuel stop database is empty. "
                        "Run: python manage.py build_fuel_cache"
                    )
                },
                status=503,
            )

        try:
            chosen_stops, total_cost = optimizer.select_fuel_stops(
                route["geometry"],
                route["distance_meters"],
                stops,
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=422)
        except Exception as exc:
            logger.exception("Optimizer failed")
            return JsonResponse({"error": f"Optimization error: {exc}"}, status=500)

        return JsonResponse(
            {
                "start": start,
                "finish": finish,
                "total_distance_miles": round(
                    route["distance_meters"] / _METERS_PER_MILE, 2
                ),
                "total_fuel_cost": total_cost,
                "route_geometry": {
                    "type": "LineString",
                    "coordinates": route["geometry"],
                },
                "fuel_stops": chosen_stops,
            }
        )
