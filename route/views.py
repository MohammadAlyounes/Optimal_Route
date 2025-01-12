from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
import time
import requests
import route.utils
import os
from django.conf import settings
import pandas as pd
from pathlib import Path

# Get the absolute path to your project root
BASE_DIR = Path(__file__).resolve().parent.parent

csv_path = os.path.join(BASE_DIR, 'data', 'fuel.csv')
csv_path2 = os.path.join(BASE_DIR, 'data', 'locations.csv')

# Add error handling for file reading
try:
    fuel_data = pd.read_csv(csv_path)
    locations_data = pd.read_csv(csv_path2)

except FileNotFoundError as e:
    print(f"Current directory: {os.getcwd()}")
    print(f"Looking for file at: {csv_path}")
    print(f"Error: {e}")
    raise


# Retry mechanism with exponential backoff
def make_request_with_retry(func, max_retries=5, backoff_factor=1):
    retries = 0
    while retries < max_retries:
        try:
            result = func()
            return result
        except requests.exceptions.RequestException as e:
            if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 429:
                # Rate limit exceeded
                retry_after = int(e.response.headers.get("Retry-After", backoff_factor * (2 ** retries)))
                print(f"Rate limit exceeded. Retrying in {retry_after} seconds...")
                time.sleep(retry_after)
                retries += 1
            else:
                # Re-raise other HTTP errors
                raise
    raise Exception(f"Request failed after {max_retries} retries.")


@api_view(['POST'])
def route_details(request):
    start = request.data.get("start")  # Retrieve start location
    end = request.data.get("end")  # Retrieve end location

    # Geocode start and end locations with retry
    start_coords = make_request_with_retry(
        lambda: route.utils.geocode_location(start.get("state"), start.get("city"), start.get("address"))
    )
    end_coords = make_request_with_retry(
        lambda: route.utils.geocode_location(end.get("state"), end.get("city"), end.get("address"))
    )

    # Extract waypoints with retry
    route_waypoints = make_request_with_retry(
        lambda: route.utils.extract_waypoints(start_coords, end_coords, spacing_miles=25)
    )
    # Extract waypoints with retry
    route_waypoints = make_request_with_retry(
        lambda: route.utils.extract_waypoints(start_coords, end_coords, spacing_miles=25)
    )

    route_distance = route.utils.haversine_distance(
        start_coords["lat"], start_coords["lng"], end_coords["lat"], end_coords["lng"]
    )

    # Find fuel stops with retry
    fuel_stops = make_request_with_retry(
        lambda: route.utils.find_fuel_stops(
            route_distance, fuel_data, route_waypoints, locations_data, vehicle_range=500, search_radius=10,
            cost_threshold=0.1
        )
    )

    # Plot route
    route_map = make_request_with_retry(
        lambda: route.utils.plot_route_interactive(
            start_coords["lat"], start_coords["lng"], end_coords["lat"], end_coords["lng"], route_waypoints
        )
    )

    # Calculate total cost
    total_cost = sum([stop["PricePerGallon"] * 50 for stop in fuel_stops])

    # Format fuel stops
    formatted_fuel_stops = [{
        "station_name": stop.get("FuelStation"),
        "address": stop.get("Address"),
        "price_per_gallon": stop.get("PricePerGallon"),
        "DistanceToStation": stop.get("distance_to_station")
    } for stop in fuel_stops]

    # Prepare response
    response_data = {
        "status": "success",
        "data": {
            "route_map": route_map,
            "fuel_stops": formatted_fuel_stops,
            "total_cost": round(total_cost, 3),
            "total_distance": round(route_distance, 2),
            "number_of_stops": len(fuel_stops)
        }
    }

    return Response(response_data, status=status.HTTP_200_OK)


