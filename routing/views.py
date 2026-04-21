"""HTTP view for route and optimal fuel-stop planning."""

import json
import logging

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .services import ors_client, fuel_data, optimizer
from .services.fuel_data import FuelStop

logger = logging.getLogger(__name__)

_METERS_PER_MILE = 1609.344

_fuel_stops: list[FuelStop] | None = None
_geohash_index: dict | None = None


def _get_fuel_data() -> tuple[list[FuelStop], dict]:
    global _fuel_stops, _geohash_index
    if _fuel_stops is None:
        _fuel_stops, _geohash_index = fuel_data.load_fuel_stops()
    return _fuel_stops, _geohash_index


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
            (start_lat, start_lon), (finish_lat, finish_lon) = (
                ors_client.geocode_pair(start, finish)
            )
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("Geocoding failed")
            return JsonResponse({"error": f"Geocoding error: {exc}"}, status=502)

        try:
            route = ors_client.get_route(start_lat, start_lon, finish_lat, finish_lon)
        except Exception as exc:
            logger.exception("Routing failed")
            return JsonResponse({"error": f"Routing error: {exc}"}, status=502)

        try:
            stops, geo_index = _get_fuel_data()
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
                geohash_index=geo_index,
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
