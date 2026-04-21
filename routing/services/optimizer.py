"""
Fuel stop selection optimizer.

Algorithm overview:
  1. Project each fuel stop onto the route polyline: find the closest polyline
     point and record cumulative route distance at that point.
  2. Filter stops beyond ROUTE_CORRIDOR_MILES from the polyline.
  3. Run dynamic programming over the sorted stop positions to find the
     minimum-cost sequence of fuel stops that keeps the vehicle within
     range (500 miles) at all times.

DP formulation:
  Nodes: start(0) + eligible stops + finish(D)
  State dp[i] = min total fuel cost to reach node i (arriving with any fuel level)
  Transition: for each i -> j where dist(i,j) <= MAX_RANGE:
      gallons = dist(i, j) / MPG
      dp[j] = min(dp[j], dp[i] + gallons * price[i])
  (Start and finish have price=0 — no purchase happens at destination.)
"""

from __future__ import annotations

import math
from typing import TypedDict

import numpy as np
from django.conf import settings

from .fuel_data import FuelStop

MAX_RANGE_MILES = 500.0
MPG = 10.0
METERS_PER_MILE = 1609.344


class StopResult(TypedDict):
    name: str
    address: str
    city: str
    state: str
    lat: float
    lon: float
    price_per_gallon: float
    gallons: float
    cost: float
    position_miles: float


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in miles."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _cumulative_distances_miles(coords: list[list[float]]) -> np.ndarray:
    """
    Given a list of [lon, lat] polyline coordinates, return a 1-D array of
    cumulative distances (in miles) from the first point to each subsequent point.
    """
    lons = np.array([c[0] for c in coords])
    lats = np.array([c[1] for c in coords])

    R = 3958.8
    lat_r = np.radians(lats)
    lon_r = np.radians(lons)

    dlat = np.diff(lat_r)
    dlon = np.diff(lon_r)
    lat1 = lat_r[:-1]
    lat2 = lat_r[1:]

    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    seg_dist = 2 * R * np.arcsin(np.sqrt(a))

    cum = np.zeros(len(coords))
    cum[1:] = np.cumsum(seg_dist)
    return cum


def _project_stop_onto_route(
    stop_lat: float,
    stop_lon: float,
    poly_lats: np.ndarray,
    poly_lons: np.ndarray,
    cum_dist: np.ndarray,
    corridor_miles: float,
) -> tuple[float, float] | None:
    """
    Find the closest point on the polyline (projected onto each segment) to the stop.
    Returns (route_position_miles, distance_to_route_miles) or None if beyond corridor.

    Uses a planar approximation in miles, which is accurate enough for the corridor
    filter (max ~5-10 miles deviation at continental US latitudes).
    """
    # Convert degrees to approximate miles relative to the stop's latitude
    lat_scale = 69.0
    lon_scale = 69.0 * math.cos(math.radians(stop_lat))

    qy = stop_lat * lat_scale
    qx = stop_lon * lon_scale

    py = poly_lats * lat_scale
    px = poly_lons * lon_scale

    best_dist = float("inf")
    best_pos = 0.0

    for i in range(len(poly_lats) - 1):
        ay, ax = py[i], px[i]
        by, bx = py[i + 1], px[i + 1]

        seg_len_sq = (bx - ax) ** 2 + (by - ay) ** 2
        if seg_len_sq < 1e-12:
            # Degenerate segment — use vertex distance
            dist = math.hypot(qx - ax, qy - ay)
            t = 0.0
        else:
            t = ((qx - ax) * (bx - ax) + (qy - ay) * (by - ay)) / seg_len_sq
            t = max(0.0, min(1.0, t))
            cx = ax + t * (bx - ax)
            cy = ay + t * (by - ay)
            dist = math.hypot(qx - cx, qy - cy)

        if dist < best_dist:
            best_dist = dist
            best_pos = cum_dist[i] + t * (cum_dist[i + 1] - cum_dist[i])

    if best_dist > corridor_miles:
        return None
    return float(best_pos), best_dist


def select_fuel_stops(
    route_coords: list[list[float]],
    total_distance_meters: float,
    fuel_stops: list[FuelStop],
) -> tuple[list[StopResult], float]:
    """
    Select the minimum-cost fuel-stop sequence for the route.

    Returns:
      - List of chosen fuel stops with purchase details.
      - Total fuel cost in dollars.
    """
    corridor_miles: float = getattr(settings, "ROUTE_CORRIDOR_MILES", 5)
    total_miles = total_distance_meters / METERS_PER_MILE

    poly_lats = np.array([c[1] for c in route_coords])
    poly_lons = np.array([c[0] for c in route_coords])
    cum_dist = _cumulative_distances_miles(route_coords)

    # --- Project stops onto route, filter by corridor ---
    projected: list[dict] = []
    for stop in fuel_stops:
        result = _project_stop_onto_route(
            stop["lat"], stop["lon"],
            poly_lats, poly_lons, cum_dist, corridor_miles,
        )
        if result is None:
            continue
        pos_miles, _ = result
        projected.append({**stop, "pos": pos_miles})

    # Sort by route position
    projected.sort(key=lambda s: s["pos"])

    # Build node list: start + eligible stops + finish
    # start: pos=0, price=0; finish: pos=total_miles, price=0
    START = {"pos": 0.0, "price": 0.0, "name": "__start__"}
    FINISH = {"pos": total_miles, "price": 0.0, "name": "__finish__"}
    nodes = [START] + projected + [FINISH]
    n = len(nodes)
    finish_idx = n - 1

    # --- DP ---
    INF = float("inf")
    dp = [INF] * n
    dp[0] = 0.0
    prev = [-1] * n

    for i in range(n - 1):
        if dp[i] == INF:
            continue
        for j in range(i + 1, n):
            dist_ij = nodes[j]["pos"] - nodes[i]["pos"]
            if dist_ij > MAX_RANGE_MILES:
                break
            # Buy exactly enough fuel at i to reach j
            candidate = dp[i] + (dist_ij / MPG) * nodes[i]["price"]
            if candidate < dp[j]:
                dp[j] = candidate
                prev[j] = i

    if dp[finish_idx] == INF:
        raise ValueError(
            "No feasible route found: not enough fuel stops within 500-mile windows."
        )

    # Reconstruct path
    path: list[int] = []
    cur = finish_idx
    while cur != -1:
        path.append(cur)
        cur = prev[cur]
    path.reverse()

    # Build result list (skip start and finish pseudo-nodes)
    results: list[StopResult] = []
    total_cost = 0.0
    for k in range(len(path) - 1):
        node_idx = path[k]
        next_idx = path[k + 1]
        node = nodes[node_idx]
        if node["name"] == "__start__":
            continue  # no purchase at start
        dist_to_next = nodes[next_idx]["pos"] - node["pos"]
        gallons = dist_to_next / MPG
        cost = gallons * node["price"]
        total_cost += cost
        results.append(
            StopResult(
                name=node["name"],
                address=node["address"],
                city=node["city"],
                state=node["state"],
                lat=node["lat"],
                lon=node["lon"],
                price_per_gallon=round(node["price"], 4),
                gallons=round(gallons, 3),
                cost=round(cost, 2),
                position_miles=round(node["pos"], 2),
            )
        )

    # Add start-to-first-stop fuel cost (bought at start if applicable)
    # The start node has price=0, so no cost — handled by DP already.

    return results, round(total_cost, 2)
