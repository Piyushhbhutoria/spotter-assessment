## Spotter assessment — route + fuel planner API

Django 6 app with a single JSON API: given US start and finish addresses, it returns an OpenRouteService route geometry, suggested fuel stops (500 mi range, 10 MPG, prices from the bundled CSV), and total fuel cost. Fuel-stop coordinates are served from a local geocode cache built from the CSV.

**Stack:** Django, Django REST Framework (views are plain `JsonResponse`), OpenRouteService (geocoding + driving directions), NumPy/geohash for corridor search, ArcGIS (via geopy) only when building the fuel cache.

---

## Assignment

- Build an API for start/finish locations in the USA; return the route map and cost-effective fuel stops; 500 mi max range; 10 MPG; use the provided fuel price file; pick a free map/routing API.
- Prefer fast responses and minimal external routing API calls (ideally one).

---

## Prerequisites

- [Conda](https://docs.conda.io/) (recommended; `environment.yml` pins Python 3.12 and dependencies).
- An [OpenRouteService](https://openrouteservice.org/) API key (`ORS_API_KEY`).

---

## Setup

1. **Create and activate the environment**

   ```bash
   conda env create -f environment.yml
   conda activate spotter-assessment
   ```

2. **Configure secrets**

   Copy `.env.example` to `.env` and set your key (no quotes needed if the value has no spaces):

   ```bash
   cp .env.example .env
   # Edit .env: ORS_API_KEY=your_key_here
   ```

3. **Initialize the database** (SQLite; needed for Django’s built-in apps)

   ```bash
   python manage.py migrate
   ```

4. **Build the fuel geocode cache** (reads `Fuel Prices Assessment.csv`, writes `fuel_geocode_cache.json`)

   One-time; typically a few minutes (geocoding via ArcGIS). Required before the API can return fuel stops.

   ```bash
   python manage.py build_fuel_cache
   ```

   To rebuild everything: `python manage.py build_fuel_cache --force`

5. **Sanity check**

   ```bash
   python manage.py check
   ```

---

## Run the development server

```bash
python manage.py runserver
```

Default: `http://127.0.0.1:8000/`. The route endpoint is `POST /api/route/`.

---

## Tests

With the conda env activated, from the project root:

```bash
python manage.py test
```

This runs Django’s test discovery (e.g. `routing/tests/`).

---

## Benchmarks

End-to-end HTTP timings and an in-process `cProfile` pass live in `_bench.py`. **Start the server first** (same host/port as below), then in another shell:

```bash
conda activate spotter-assessment
python _bench.py
```

The script POSTs several fixed routes to `http://127.0.0.1:8000/api/route/` (cold vs warm) and prints cumulative profile stats for the routing/fuel pipeline. If nothing is listening on port 8000, it exits with an error.

---

## API

**Endpoint:** `POST /api/route/`  
**Body (JSON):**

```json
{
  "start": "Chicago, IL",
  "finish": "Los Angeles, CA"
}
```

**Successful response (shape):**

- `start`, `finish` — echoed strings
- `total_distance_miles` — route length
- `total_fuel_cost` — dollars at 10 MPG using CSV prices
- `route_geometry` — GeoJSON-like `LineString` with `[lon, lat]` coordinates
- `fuel_stops` — selected stops along the route

Errors return JSON with an `error` field and an appropriate HTTP status (400 / 422 / 500 / 502 / 503). If fuel data is missing, run `build_fuel_cache` as above.

---

## Project layout (short)

| Path | Role |
|------|------|
| `config/` | Django settings, root URLs |
| `routing/` | API view, services (ORS, fuel load, optimizer) |
| `Fuel Prices Assessment.csv` | Fuel price source |
| `fuel_geocode_cache.json` | Geocoded stations (generated) |
| `_bench.py` | HTTP + profiling benchmark |
| `environment.yml` | Conda env definition |
