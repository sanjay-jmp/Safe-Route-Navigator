from flask import Flask, request, jsonify
from flask_cors import CORS
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString
import os
import requests

app = Flask(__name__)
CORS(app)

GRAPHML_URL = "https://www.dropbox.com/scl/fi/6req40ef4vtwxq2p3v0hq/los_angeles_precomputed_severity.graphml?rlkey=cfnvpilssp2d11217s7n3zbil&st=apubg9se&dl=1"
GRAPHML_PATH = "los_angeles.graphml"

if not os.path.exists(GRAPHML_PATH):
    print("📥 Downloading .graphml file from Dropbox...")
    response = requests.get(GRAPHML_URL)
    response.raise_for_status()

    with open(GRAPHML_PATH, "wb") as f:
        f.write(response.content)

    # Check for HTML fallback page (should not exist)
    with open(GRAPHML_PATH, "rb") as f:
        head = f.read(300)
        if b"<html" in head.lower():
            raise Exception("❌ Dropbox returned HTML — check if file is public or if the link is correct.")

    print(f"✅ Download complete! File size: {os.path.getsize(GRAPHML_PATH)} bytes")

# Load the graph
print("🔄 Loading graph from .graphml...")
G = ox.load_graphml(GRAPHML_PATH)



# ────────────────────────────────────────
# ✅ Get available time bins from graph attributes
sample_edge = next(iter(G.edges(data=True)))[-1]
available_time_bins = [
    key.replace("severity_", "") for key in sample_edge.keys() if key.startswith("severity_")
]

def get_time_bin(user_time):
    """Returns the closest available time bin for a given user time."""
    user_hour = int(user_time.split(":")[0])
    selected_bin = available_time_bins[0]
    for bin_time in sorted(available_time_bins):
        bin_hour = int(bin_time.split(":")[0])
        if user_hour >= bin_hour:
            selected_bin = bin_time
    return selected_bin

def severity_level(avg_severity):
    if avg_severity >= 7:
        return "Low"
    elif avg_severity >= 4:
        return "Medium"
    else:
        return "High"

def find_route(G, source_lat, source_lon, dest_lat, dest_lon, time_bin, route_type):
    severity_attr = f"severity_{time_bin}"

    # Prepare severity attribute
    for u, v, data in G.edges(data=True):
        data[severity_attr] = float(data.get(severity_attr, float('inf')))

    # Find nearest nodes
    start_node = ox.distance.nearest_nodes(G, source_lon, source_lat)
    end_node = ox.distance.nearest_nodes(G, dest_lon, dest_lat)

    # Weight logic
    if route_type == "safest":
        weight = severity_attr
    elif route_type == "fastest":
        weight = "length"
    elif route_type == "safest_fastest":
        def custom_weight(u, v, data):
            severity = data.get(severity_attr, float('inf'))
            length = data.get("length", float('inf'))
            return 0.5 * severity + 0.5 * length
        weight = custom_weight
    else:
        return {"error": "Invalid route_type"}, 400

    try:
        path = nx.astar_path(G, start_node, end_node, weight=weight)
    except nx.NetworkXNoPath:
        return {"error": "No route found"}, 404

    # Gather route coordinates and stats
    route_coords = []
    total_distance = 0
    total_severity = 0

    for u, v in zip(path[:-1], path[1:]):
        edge_data = G.get_edge_data(u, v)
        best_edge = min(edge_data.values(), key=lambda d: d.get(severity_attr, float('inf')))
        total_distance += best_edge.get("length", 0)
        total_severity += best_edge.get(severity_attr, 0)

        if 'geometry' in best_edge:
            coords = list(best_edge['geometry'].coords)
            coords = [(lat, lon) for lon, lat in coords]
            route_coords.extend(coords)
        else:
            route_coords.append((G.nodes[u]['y'], G.nodes[u]['x']))
            route_coords.append((G.nodes[v]['y'], G.nodes[v]['x']))

    avg_severity = total_severity / len(path)
    estimated_minutes = total_distance / 1000 / 30 * 60  # Assuming 30 km/h

    route_info = {
        "start_node": start_node,
        "dest_node": end_node,
        "distance": f"{total_distance / 1000:.2f} km",
        "duration": f"{int(estimated_minutes)} min",
        "safety_level": severity_level(avg_severity),
        "safety_score": round(avg_severity, 2)
    }

    return {
        "route": route_coords,
        "info": route_info
    }

@app.route('/')
def home():
    return "✅ Safe Route Finder API is live!"

@app.route('/find_safe_route', methods=['GET'])
def get_safe_route():
    source = request.args.get('source')
    destination = request.args.get('destination')
    user_time = request.args.get('time')
    route_type = request.args.get('route_type', 'safest')

    if not source or not destination or not user_time:
        return jsonify({"error": "Missing required parameters"}), 400

    try:
        src_lat, src_lon = map(float, source.split(','))
        dest_lat, dest_lon = map(float, destination.split(','))
    except ValueError:
        return jsonify({"error": "Invalid coordinates"}), 400

    time_bin = get_time_bin(user_time)
    result = find_route(G, src_lat, src_lon, dest_lat, dest_lon, time_bin, route_type)

    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))  # Get PORT from Render
    app.run(host='0.0.0.0', port=port)        # Bind to 0.0.0.0 so Render can access it

