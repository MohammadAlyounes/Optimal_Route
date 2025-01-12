# This file is used for our domain-specific app, 'route'. It provides us with general functions for implementation.

import requests
import math
import openrouteservice
import folium

api_key = "YOUR_API_KEY"


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the distance between two points (locations) on Earth using the Haversine formula.
    """
    R = 6371  # Radius of Earth in km
    d_lat = math.radians(lat2 - lat1)  # Distance between two latitude locations.
    d_lon = math.radians(lon2 - lon1)  # Distance between two longitude locations.
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(
        d_lon / 2) ** 2  # Haversine distance formula
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c * 0.621371  # Convert km to miles


def geocode_location(state, city, address):
    """
    Get a location info as a parameters and return the corresponding latitude and longitude as dictionary.
    """

    url = f"https://api.openrouteservice.org/geocode/search"  # Use the OpenRouteService as our API in the project
    query = f"{address}, {city}, {state}"
    params = {
        "api_key": api_key,
        "text": query,
        "boundary.country": "US",
    }

    response = requests.get(url, params)
    if response.status_code == 200:
        data = response.json()
        if "features" in data and len(data["features"]) > 0:
            coordinates = data["features"][0]["geometry"]["coordinates"]
            return {"lat": coordinates[1], "lng": coordinates[0]}
        else:
            raise ValueError(f"No geocoding results for {query}")
    else:
        raise Exception(f"Geocoding API Error: {response.status_code} - {response.text}")

# Test example
# print(geocode_location("OK", "Big Cabin", "1-44, EXIT 283 & US-69"))


def extract_waypoints(start_location, end_location, spacing_miles=25):
    """
    Extracts a list of  waypoints between start and end locations along our route (GeoJSON).
    """
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {"Authorization": api_key}
    payload = {
        "coordinates": [
            [start_location["lng"], start_location["lat"]],
            [end_location["lng"], end_location["lat"]]
        ],
        "instructions": False  # Disable detailed instructions to simplify response
    }

    # Make the API request
    response = requests.post(url, json=payload, headers=headers)

    # Check for errors in the response
    if response.status_code != 200:
        error_response = response.json()
        error_message = error_response.get("error", {}).get("message", "Unknown error")
        raise Exception(f"ORS API Error: {response.status_code} - {error_message}")

    # Parse the route data
    route_data = response.json()

    # Decode the polyline into coordinates
    encoded_geometry = route_data["routes"][0]["geometry"]
    coordinates = openrouteservice.convert.decode_polyline(encoded_geometry)["coordinates"]

    # Process coordinates to extract waypoints every `spacing_miles` miles
    waypoints = []
    cumulative_distance = 0
    last_added_point = None

    for i in range(1, len(coordinates)):
        prev_point = coordinates[i - 1]
        curr_point = coordinates[i]

        # Calculate distance between consecutive points
        segment_distance = haversine_distance(
            prev_point[1], prev_point[0], curr_point[1], curr_point[0]
        )
        cumulative_distance += segment_distance

        # Add waypoint if cumulative distance exceeds spacing
        if cumulative_distance >= spacing_miles:
            waypoints.append({"lat": curr_point[1], "lng": curr_point[0]})
            cumulative_distance = 0  # Reset cumulative distance
            last_added_point = curr_point

    # Ensure the endpoint is included as the last waypoint
    if last_added_point != coordinates[-1]:
        waypoints.append({"lat": coordinates[-1][1], "lng": coordinates[-1][0]})

    return waypoints


def get_current_state(waypoint, locations):
    """
    Matches a waypoint to the closest city and extracts the state.

    Parameters:
    - waypoint: A dictionary with lat and lng.
    - locations: DataFrame with city, state, and coordinates.

    Returns:
    - State name (e.g., "CA") for the closest city.
    """
    lat1, lon1 = waypoint["lat"], waypoint["lng"]
    min_distance = float('inf')
    current_state = None

    for _, row in locations.iterrows():
        city_coords = geocode_location(row["State"], row["City"], row["Address"])
        distance = haversine_distance(lat1, lon1, city_coords["lat"], city_coords["lng"])
        if distance < min_distance:
            min_distance = distance
            current_state = row["State"]

    return current_state


def find_fuel_stops(distance, fuel_data, waypoints, locations, vehicle_range=500, search_radius=10, cost_threshold=0.1):
    """
    Finds optimal fuel stops along the route, filtered by the current state.

    Parameters:
    - distance: Total distance of the route in miles.
    - fuel_data: DataFrame containing fuel station details.
    - waypoints: List of waypoints (lat, lng) along the route.
    - locations: DataFrame with city, state, and address.
    - vehicle_range: Maximum range of the vehicle in miles.
    - search_radius: Search radius in miles for nearby fuel stations.
    - cost_threshold: Cost advantage threshold to trigger early refueling (e.g., 0.1 for 10% cheaper).

    Returns:
    - List of optimal fuel stops along the route.
    """
    stops = []
    remaining_range = vehicle_range  # Start with a full tank
    total_distance_covered = 0  # Track the distance covered

    for i, waypoint in enumerate(waypoints):
        # Calculate the remaining distance to the destination
        distance_to_finish = distance - total_distance_covered

        # Determine the current state based on the waypoint
        current_state = get_current_state(waypoint, locations)

        # Filter fuel stations for the current state
        state_stations = fuel_data[fuel_data["State"] == current_state]

        # Find nearby fuel stations for the current waypoint
        nearby_stations = []

        for _, station in state_stations.iterrows():
            station_coords = geocode_location(station["State"], station["City"], station["Address"])
            distance_to_station = haversine_distance(
                waypoint["lat"], waypoint["lng"], station["lat"], station["lng"]
            )
            if distance_to_station <= search_radius:
                nearby_stations.append({
                    "FuelStation": station["Truckstop Name"],
                    "Address": station["Address"],
                    "PricePerGallon": station["Retail Price"],
                    "DistanceToStation": distance_to_station
                })

        # Sort nearby stations by price and distance
        nearby_stations = sorted(
            nearby_stations, key=lambda x: (x["PricePerGallon"], x["DistanceToStation"])
        )

        # Predict future costs (look ahead to next waypoints)
        future_stations = []
        for future_waypoint in waypoints[i + 1:i + 3]:  # Check the next 2 waypoints
            future_state = get_current_state(future_waypoint, locations)
            future_state_stations = fuel_data[fuel_data["State"] == future_state]
            for _, station in future_state_stations.iterrows():
                future_station_coords = geocode_location(station["State"], station["City"], station["Address"])
                distance_to_station = haversine_distance(
                    future_waypoint["lat"], future_waypoint["lng"], future_station["lat"], future_station["lng"]
                )
                if distance_to_station <= search_radius:
                    future_stations.append(station["Retail Price"])

        future_avg_cost = sum(future_stations) / len(future_stations) if future_stations else float('inf')

        # Check refueling conditions
        refuel_now = False
        if remaining_range < min(distance_to_finish, vehicle_range * 0.2):
            refuel_now = True  # Low range forces refueling
        elif nearby_stations and nearby_stations[0]["PricePerGallon"] < future_avg_cost * (1 - cost_threshold):
            refuel_now = True  # Early refuel for cost savings

        # Perform refueling if required
        if refuel_now and nearby_stations:
            best_stop = nearby_stations[0]
            stops.append(best_stop)
            remaining_range = vehicle_range  # Refill the tank

        # Update remaining range and distance covered
        remaining_range -= search_radius
        total_distance_covered += search_radius

    return stops


def plot_route_interactive(lat1, lng1, lat2, lng2, route_coordinates):
    # Create a map centered at the midpoint
    midpoint = [(lat1 + lat2) / 2, (lng1 + lng2) / 2]
    m = folium.Map(location=midpoint, zoom_start=7)

    # Add the route
    folium.PolyLine(route_coordinates, color="red", weight=2.5, opacity=1).add_to(m)

    # Add start and end markers
    folium.Marker(location=[lat1, lng1], popup="Start", icon=folium.Icon(color="green")).add_to(m)
    folium.Marker(location=[lat2, lng2], popup="End", icon=folium.Icon(color="blue")).add_to(m)

    # Display the map
    return m._repr_html_()