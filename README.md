## Assignment

- Build an API that takes inputs of start and finish location both within the USA
- Return a map of the route along with optimal location to fuel up along the route -- optimal mostly means cost effective based on fuel prices
- Assume the vehicle has a maximum range of 500 miles so multiple fuel ups might need to be displayed on the route
- Also return the total money spent on fuel assuming the vehicle achieves 10 miles per gallon
- Use the attached file for a list of fuel prices
- Find a free API yourself for the map and routing

## Requirements

- Build the app in latest stable Django
- use conda to create a new environment and install the dependencies

## Additional Requirements

- The API should return results quickly, the quicker the better
- The API shouldn't need to call the free map/routing API you found too much. One call to the map/route API is ideal, two or three is acceptable

## Setup (Conda + Django)

- Create env from file: `conda env create -f environment.yml`
- Activate env: `conda activate spotter-assessment`
- Copy `.env.example` to `.env` and fill in your [OpenRouteService API key](https://openrouteservice.org/)
- Build the fuel-stop geocode cache (one-time, ~2-3 min): `python manage.py build_fuel_cache`
- Run checks: `python manage.py check`
- Start dev server: `python manage.py runserver`

## API Usage

```
POST /api/route/
Content-Type: application/json

{ "start": "Chicago, IL", "finish": "Los Angeles, CA" }
```
