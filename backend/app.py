from flask import Flask, request, jsonify
from flask_cors import CORS
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString
from neo4j import GraphDatabase, basic_auth
import math
import os
import functools

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "https://lively-youtiao-bace0b.netlify.app"}})

# --- Neo4j AuraDB Connection Details (NEW INSTANCE) ---
# Replace these with your new AuraDB credentials
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j+s://<YOUR_NEW_INSTANCE_ID>.databases.neo4j.io")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "<YOUR_NEW_PASSWORD>")

# --- Global Neo4j Driver ---
driver = None
try:
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=basic_auth(NEO4J_USERNAME, NEO4J_PASSWORD),
        connection_timeout=60
    )
    driver.verify_connectivity()
    print("✅ Successfully connected to NEW Neo4j AuraDB instance!")
except Exception as e:
    print(f"❌ Failed to connect to Neo4j AuraDB: {e}")
    print("Please ensure your new AuraDB instance is active and credentials are correct.")

# --- Available Time Bins ---
available_time_bins = ["00:00:00", "03:00:00", "06:00:00", "09:00:00",
                       "12:00:00", "15:00:00", "18:00:00", "21:00:00"]

# --- Helper Functions ---
def get_time_bin(user_time):
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

# --- Cached Function to Fetch Subgraph ---
@functools.lru_cache(maxsize=1)
def _get_cached_subgraph(min_lat, max_lat, min_lon, max_lon, time_bin):
    if driver is None:
        raise Exception("Neo4j driver not initialized.")
    
    severity_attr = f"severity_{time_bin}"
    local_G = nx.MultiDiGraph()
    local_G.graph["crs"] = "epsg:4326"
    local_G.graph["bbox"] = (max_lat, min_lat, max_lon, min_lon)

    with driver.session() as session:
        try:
            # --- Fetch Nodes ---
            nodes_query = """
            MATCH (n:Location)
            WHERE n.latitude >= $min_lat AND n.latitude <= $max_lat
              AND n.longitude >= $min_lon AND n.longitude <= $max_lon
            RETURN n.id AS id, n.latitude AS latitude, n.longitude AS longitude
            """
            nodes_result = session.run(nodes_query, min_lat=min_lat, max_lat=max_lat,
                                       min_lon=min_lon, max_lon=max_lon).data()

            if not nodes_result:
                print(f"No nodes found in bbox: {min_lat}-{max_lat}, {min_lon}-{max_lon}")
                return None

            for record in nodes_result:
                osmid = record["id"]
                lat = record["latitude"]
                lon = record["longitude"]
                local_G.add_node(osmid, x=lon, y=lat, osmid=osmid)

            # --- Fetch Edges ---
            osmids_in_bbox = list(local_G.nodes())
            edges_query = f"""
            MATCH (u:Location)-[r:ROAD]->(v:Location)
            WHERE u.id IN $osmids AND v.id IN $osmids
            RETURN 
                u.id AS u_id, v.id AS v_id, r.length AS length,
                r.{severity_attr} AS severity_val, r.geometry_coords AS geometry_coords
            """
            edges_result = session.run(edges_query, osmids=osmids_in_bbox).data()

            for record in edges_result:
                u = record["u_id"]
                v = record["v_id"]
                length = float(record["length"]) if record["length"] else 0.0
                severity = float(record["severity_val"]) if record["severity_val"] else 0.0
                geometry_coords_flat = record["geometry_coords"]

                geometry_obj = None
                if geometry_coords_flat and len(geometry_coords_flat) % 2 == 0:
                    coords_tuples = [(geometry_coords_flat[i], geometry_coords_flat[i+1])
                                     for i in range(0, len(geometry_coords_flat), 2)]
                    geometry_obj = LineString(coords_tuples)

                local_G.add_edge(u, v, length=length,
                                 **{severity_attr: severity},
                                 geometry=geometry_obj)
            
            print(f"✅ Subgraph built: {len(local_G.nodes)} nodes, {len(local_G.edges)} edges.")
            return local_G

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error fetching subgraph: {e}")
            return None

# --- Route Finder ---
def _find_route_from_neo4j_data(source_lat, source_lon, dest_lat, dest_lon, time_bin, route_type, buffer_degrees=0.06):
    min_lat = min(source_lat, dest_lat) - buffer_degrees
    max_lat = max(source_lat, dest_lat) + buffer_degrees
    min_lon = min(source_lon, dest_lon) - buffer_degrees
    max_lon = max(source_lon, dest_lon) + buffer_degrees

    local_G = _get_cached_subgraph(min_lat, max_lat, min_lon, max_lon, time_bin)
    if local_G is None:
        return None, None, {"error": "No graph data for this region."}

    severity_attr = f"severity_{time_bin}"

    try:
        start_node = ox.distance.nearest_nodes(local_G, source_lon, source_lat)
        end_node = ox.distance.nearest_nodes(local_G, dest_lon, dest_lat)

        if route_type == "safest":
            weight = severity_attr
        elif route_type == "fastest":
            weight = "length"
        else:
            def combined_weight(u, v, data):
                return 0.5 * data.get("length", 1) + 0.5 * data.get(severity_attr, 1)
            weight = combined_weight

        path = nx.astar_path(local_G, start_node, end_node, weight=weight)

        total_dist = sum(local_G[u][v][0].get("length", 0) for u, v in zip(path[:-1], path[1:]))
        avg_severity = sum(local_G[u][v][0].get(severity_attr, 0) for u, v in zip(path[:-1], path[1:])) / len(path)
        est_minutes = total_dist / 1000 / 30 * 60

        info = {
            "distance": f"{total_dist / 1000:.2f} km",
            "duration": f"{int(est_minutes)} min",
            "safety_level": severity_level(avg_severity),
            "safety_score": round(avg_severity, 2)
        }
        return local_G, path, info

    except nx.NetworkXNoPath:
        return None, None, {"error": "No route found."}

# --- Routes ---
@app.route('/')
def home():
    return jsonify({"status": "success", "message": "Safe Route Navigator Backend (Connected to New Neo4j)"})

@app.route('/find_safe_route', methods=['GET'])
def get_safe_route():
    try:
        source = request.args.get("source")
        destination = request.args.get("destination")
        user_time = request.args.get("time")
        route_type = request.args.get("route_type", "safest")

        src_lat, src_lon = map(float, source.split(","))
        dest_lat, dest_lon = map(float, destination.split(","))

        time_bin = get_time_bin(user_time)
        local_G, path, info = _find_route_from_neo4j_data(src_lat, src_lon, dest_lat, dest_lon, time_bin, route_type)

        if local_G and path:
            coords = [(local_G.nodes[n]['y'], local_G.nodes[n]['x']) for n in path]
            return jsonify({"status": "success", "route": coords, "info": info})
        else:
            return jsonify({"status": "error", "error": info.get("error", "Unknown error")}), 400

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
