"""Fuel-stop projection and dynamic-programming route optimization."""

from __future__ import annotations

import math
from typing import TypedDict

import numpy as np
import pygeohash as pgh

from .constants import EARTH_RADIUS_MILES, GEOHASH_PRECISION, METERS_PER_MILE
from .fuel_data import FuelStop

MAX_RANGE_MILES = 500.0
MPG = 10.0


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


def _cumulative_distances_miles(coords: list[list[float]]) -> np.ndarray:
    """
    Given a list of [lon, lat] polyline coordinates, return a 1-D array of
    cumulative distances (in miles) from the first point to each subsequent point.
    """
    lons = np.array([c[0] for c in coords])
    lats = np.array([c[1] for c in coords])

    lat_r = np.radians(lats)
    lon_r = np.radians(lons)

    dlat = np.diff(lat_r)
    dlon = np.diff(lon_r)
    lat1 = lat_r[:-1]
    lat2 = lat_r[1:]

    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    seg_dist = 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))

    cum = np.zeros(len(coords))
    cum[1:] = np.cumsum(seg_dist)
    return cum


def _project_all_stops(
    stop_lats: np.ndarray,
    stop_lons: np.ndarray,
    poly_lats: np.ndarray,
    poly_lons: np.ndarray,
    cum_dist: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Project every stop onto the route and return positions and distances."""
    lat_scale = 69.0
    mean_lat = float(poly_lats.mean())
    lon_scale = 69.0 * math.cos(math.radians(mean_lat))

    qy = stop_lats * lat_scale
    qx = stop_lons * lon_scale

    py = poly_lats * lat_scale
    px = poly_lons * lon_scale

    n_stops = len(stop_lats)
    best_dist = np.full(n_stops, np.inf)
    best_pos = np.zeros(n_stops)

    for i in range(len(py) - 1):
        ay, ax = py[i], px[i]
        by, bx = py[i + 1], px[i + 1]

        seg_dy = by - ay
        seg_dx = bx - ax
        seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy

        if seg_len_sq < 1e-12:
            dist = np.hypot(qx - ax, qy - ay)
            t_arr = np.zeros(n_stops)
        else:
            t_arr = ((qx - ax) * seg_dx + (qy - ay) * seg_dy) / seg_len_sq
            np.clip(t_arr, 0.0, 1.0, out=t_arr)
            cx = ax + t_arr * seg_dx
            cy = ay + t_arr * seg_dy
            dist = np.hypot(qx - cx, qy - cy)

        improved = dist < best_dist
        if improved.any():
            best_dist = np.where(improved, dist, best_dist)
            best_pos = np.where(
                improved,
                cum_dist[i] + t_arr * (cum_dist[i + 1] - cum_dist[i]),
                best_pos,
            )

    return best_pos, best_dist


_SAMPLE_EVERY_MILES = 10.0


def _nine_cells(cell: str) -> list[str]:
    """Return the cell itself plus its 8 adjacent geohash cells."""
    top = pgh.get_adjacent(cell, "top")
    bottom = pgh.get_adjacent(cell, "bottom")
    return [
        cell,
        top,
        bottom,
        pgh.get_adjacent(cell, "left"),
        pgh.get_adjacent(cell, "right"),
        pgh.get_adjacent(top, "left"),
        pgh.get_adjacent(top, "right"),
        pgh.get_adjacent(bottom, "left"),
        pgh.get_adjacent(bottom, "right"),
    ]


def _candidate_stops(
    route_coords: list[list[float]],
    cum_dist: np.ndarray,
    geohash_index: dict[str, list[FuelStop]],
) -> list[FuelStop]:
    """Return fuel stops from route-adjacent geohash cells."""
    seen_cells: set[str] = set()
    candidate_ids: set[int] = set()
    candidates: list[FuelStop] = []

    prev_pos = -_SAMPLE_EVERY_MILES
    for i, coord in enumerate(route_coords):
        pos = float(cum_dist[i])
        if pos - prev_pos < _SAMPLE_EVERY_MILES and i != len(route_coords) - 1:
            continue
        prev_pos = pos
        cell = pgh.encode(coord[1], coord[0], precision=GEOHASH_PRECISION)
        if cell in seen_cells:
            continue
        seen_cells.add(cell)
        for neighbor_cell in _nine_cells(cell):
            for stop in geohash_index.get(neighbor_cell, []):
                if stop["stop_id"] not in candidate_ids:
                    candidate_ids.add(stop["stop_id"])
                    candidates.append(stop)

    return candidates


def select_fuel_stops(
    route_coords: list[list[float]],
    total_distance_meters: float,
    geohash_index: dict[str, list[FuelStop]],
    corridor_miles: float,
) -> tuple[list[StopResult], float]:
    """Return the minimum-cost fuel-stop sequence and total cost."""
    total_miles = total_distance_meters / METERS_PER_MILE

    poly_lats = np.array([c[1] for c in route_coords])
    poly_lons = np.array([c[0] for c in route_coords])
    cum_dist = _cumulative_distances_miles(route_coords)

    candidates = _candidate_stops(route_coords, cum_dist, geohash_index)
    if not candidates:
        if total_miles <= MAX_RANGE_MILES:
            return [], 0.0
        raise ValueError(
            "No feasible route found: not enough fuel stops within 500-mile windows."
        )

    all_lats = np.array([s["lat"] for s in candidates])
    all_lons = np.array([s["lon"] for s in candidates])

    positions, distances = _project_all_stops(
        all_lats, all_lons, poly_lats, poly_lons, cum_dist
    )

    projected: list[dict] = [
        {**candidates[i], "pos": float(positions[i])}
        for i in range(len(candidates))
        if distances[i] <= corridor_miles
    ]

    projected.sort(key=lambda s: s["pos"])

    START = {"pos": 0.0, "price": 0.0, "name": "__start__"}
    FINISH = {"pos": total_miles, "price": 0.0, "name": "__finish__"}
    nodes = [START] + projected + [FINISH]
    n = len(nodes)
    finish_idx = n - 1

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
            candidate = dp[i] + (dist_ij / MPG) * nodes[i]["price"]
            if candidate < dp[j]:
                dp[j] = candidate
                prev[j] = i

    if dp[finish_idx] == INF:
        raise ValueError(
            "No feasible route found: not enough fuel stops within 500-mile windows."
        )

    path: list[int] = []
    cur = finish_idx
    while cur != -1:
        path.append(cur)
        cur = prev[cur]
    path.reverse()

    results: list[StopResult] = []
    total_cost = 0.0
    for k in range(len(path) - 1):
        node_idx = path[k]
        next_idx = path[k + 1]
        node = nodes[node_idx]
        if node["name"] == "__start__":
            continue
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

    return results, round(total_cost, 2)
