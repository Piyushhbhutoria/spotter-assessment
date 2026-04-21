"""Benchmark `/api/route/` end-to-end over HTTP."""
import cProfile
import io
import os
import pstats
import time
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import requests as _req
from django.conf import settings
from routing.services import ors_client, fuel_data, optimizer


_ROUTES = [
    ("Chicago, IL",   "Milwaukee, WI",    "short  ~90 mi"),
    ("Chicago, IL",   "Cleveland, OH",    "medium ~345 mi"),
    ("Chicago, IL",   "New York, NY",     "long   ~790 mi"),
    ("Chicago, IL",   "Los Angeles, CA",  "xc     ~2000 mi"),
]


def run_once(start: str, finish: str) -> tuple[float, dict]:
    t0 = time.perf_counter()
    resp = _req.post(
        "http://127.0.0.1:8000/api/route/",
        json={"start": start, "finish": finish},
        timeout=30,
    )
    elapsed = time.perf_counter() - t0
    resp.raise_for_status()
    return elapsed, resp.json()


def http_benchmark() -> None:
    print("\n=== HTTP end-to-end (two runs per route: cold, warm) ===")
    for start, finish, label in _ROUTES:
        for attempt in ("cold", "warm"):
            try:
                ms, data = run_once(start, finish)
                print(
                    f"  {attempt:4s} {label:18s} "
                    f"{ms * 1000:6.0f} ms | "
                    f"{data['total_distance_miles']:>7.1f} mi | "
                    f"{len(data['fuel_stops'])} stops | "
                    f"${data['total_fuel_cost']:.2f}"
                )
            except Exception as e:
                print(f"  {attempt:4s} {label:18s} ERROR {e}")


def profile_inproc() -> None:
    """cProfile the in-process pipeline."""
    def pipeline():
        stops, index = fuel_data.load_fuel_stops()
        (s_lat, s_lon), (f_lat, f_lon) = ors_client.geocode_pair(
            "Chicago, IL", "Los Angeles, CA"
        )
        route = ors_client.get_route(s_lat, s_lon, f_lat, f_lon)
        optimizer.select_fuel_stops(
            route["geometry"],
            route["distance_meters"],
            index,
            settings.ROUTE_CORRIDOR_MILES,
        )

    print("\n=== cProfile (in-process, warm) ===")
    pipeline()
    pr = cProfile.Profile()
    pr.enable()
    pipeline()
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(
        "spotter-assessment|requests|urllib3|optimizer|fuel_data|ors_client", 15
    )
    print(buf.getvalue())


if __name__ == "__main__":
    try:
        _req.get("http://127.0.0.1:8000/", timeout=2)
    except Exception as e:
        print(f"ERROR: server not running at 127.0.0.1:8000 ({e})")
        raise SystemExit(1)

    http_benchmark()
    profile_inproc()
