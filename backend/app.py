from flask import Flask, request, jsonify
from flask_cors import CORS
import networkx as nx
from shapely.geometry import LineString
from neo4j import GraphDatabase, basic_auth
import os
import functools

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": ["http://localhost:5173", "https://lively-youtiao-bace0b.netlify.app"]}})

# --- Neo4j AuraDB Connection ---
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j+s://c3dfe180.databases.neo4j.io")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "Vx_v-ZKi4ixRhqG1qKyae6IZhrN-D_TE5aeCvifARKo")

driver = None
try:
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=basic_auth(NEO4J_USERNAME, NEO4J_PASSWORD),
        connection_timeout=60
    )
    driver.verify_connectivity()
    print("✅ Successfully connected to Neo4j AuraDB!")
except Exception as e:
    print(f"❌ Failed to connect to Neo4j AuraDB: {e}")

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

# --- Build local graph ---
def build_local_graph(nodes, time_bin):
    severity_attr = f"severity_{time_bin}"
    G = nx.MultiDiGraph()
    G.graph["crs"] = "epsg:4326"

    # Add nodes
    for node in nodes:
        osmid = node["id"]
        lat = node["latitude"]
        lon = node["longitude"]
        G.add_node(osmid, x=lon, y=lat, osmid=osmid)

    # Fetch edges
    osmids_in_bbox = list(G.nodes())
    try:
        edges_query = """
        MATCH (u:Location)-[r:ROAD]->(v:Location)
        WHERE u.id IN $osmids AND v.id IN $osmids
        RETURN u.id AS u_id, v.id AS v_id, r.length AS length,
               r[$severity_attr] AS severity_val, r.geometry_coords AS geometry_coords
        """
        with driver.session() as session:
            edges_result = session.run(edges_query, osmids=osmids_in_bbox, severity_attr=severity_attr).data()

        for record in edges_result:
            u = record["u_id"]
            v = record["v_id"]
            length = float(record["length"]) if record["length"] else 0.0
            severity = record["severity_val"]
            try:
                severity = float(severity) if severity is not None else 0.0
            except ValueError:
                severity = 0.0

            geometry_coords_flat = record.get("geometry_coords")
            geometry_obj = None
            if geometry_coords_flat and len(geometry_coords_flat) % 2 == 0:
                geometry_obj = LineString([(geometry_coords_flat[i], geometry_coords_flat[i+1])
                                           for i in range(0, len(geometry_coords_flat), 2)])

            G.add_edge(u, v, length=length, **{severity_attr: severity}, geometry=geometry_obj)

        print(f"Graph built: {len(G.nodes)} nodes, {len(G.edges)} edges")
        return G

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error fetching edges: {e}")
        return G  # Return nodes-only graph if edges fail

# --- Find nearest node ---
def nearest_node(G, lat, lon):
    min_dist = float('inf')
    nearest = None
    for n, data in G.nodes(data=True):
        dist = (lat - data['y'])**2 + (lon - data['x'])**2
        if dist < min_dist:
            min_dist = dist
            nearest = n
    return nearest

# --- Find path ---
def find_safest_or_fastest_path(G, src_lat, src_lon, dest_lat, dest_lon, route_type, time_bin):
    severity_attr = f"severity_{time_bin}"
    src_node = nearest_node(G, src_lat, src_lon)
    dest_node = nearest_node(G, dest_lat, dest_lon)
    if src_node is None or dest_node is None:
        return None, {"error": "No nearby nodes found for source/destination."}

    weight = 'length' if route_type == 'fastest' else severity_attr
    try:
        path = nx.dijkstra_path(G, source=src_node, target=dest_node, weight=weight)
        total_length_m = sum(G[u][v][0]['length'] for u, v in zip(path[:-1], path[1:]))
        total_severity = sum(G[u][v][0].get(severity_attr, 0) for u, v in zip(path[:-1], path[1:]))
        avg_severity = total_severity / max(len(path)-1, 1)

        # Convert distance to km and duration to hours (assuming 36 km/h)
        total_length_km = round(total_length_m / 1000, 2)
        duration_h = round(total_length_km / 36, 2)  # 36 km/h avg speed

        info = {
            "distance": total_length_km,            # kilometers
            "duration": duration_h,                 # hours
            "safety_level": severity_level(avg_severity),
            "safety_score": round(avg_severity,2)
        }

        return path, info
    except nx.NetworkXNoPath:
        return None, {"error": "No path found between source and destination."}

# --- Routes ---
@app.route('/')
def home():
    return jsonify({"status": "success", "message": "Safe Route Navigator Backend"})

@app.route('/find_safe_route', methods=['GET'])
def get_safe_route():
    try:
        source = request.args.get("source")
        destination = request.args.get("destination")
        user_time = request.args.get("time")
        route_type = request.args.get("route_type", "safest").lower()
        if route_type not in ["safest", "fastest"]:
            route_type = "safest"

        src_lat, src_lon = map(float, source.split(","))
        dest_lat, dest_lon = map(float, destination.split(","))

        time_bin = get_time_bin(user_time)
        print(f"Source: {src_lat},{src_lon}")
        print(f"Destination: {dest_lat},{dest_lon}")
        print(f"Time: {user_time} → Time bin: {time_bin}")
        print(f"Route type: {route_type}")

        # Fetch nodes
        with driver.session() as session:
            delta = 0.2
            min_lat, max_lat = min(src_lat, dest_lat)-delta, max(src_lat, dest_lat)+delta
            min_lon, max_lon = min(src_lon, dest_lon)-delta, max(src_lon, dest_lon)+delta
            print(f"Bounding box: {min_lat} {max_lat} {min_lon} {max_lon}")

            nodes_query = """
            MATCH (n:Location)
            WHERE toFloat(n.y) >= $min_lat AND toFloat(n.y) <= $max_lat
              AND toFloat(n.x) >= $min_lon AND toFloat(n.x) <= $max_lon
            RETURN n.id AS id, toFloat(n.y) AS latitude, toFloat(n.x) AS longitude
            """
            result = session.run(nodes_query, min_lat=min_lat, max_lat=max_lat,
                                  min_lon=min_lon, max_lon=max_lon).data()
            print(f"Nodes fetched: {len(result)}")
            if not result:
                return jsonify({"status":"error","error":"No nodes found in the area"}), 400

        # Build graph and find path
        G = build_local_graph(result, time_bin)
        path, info = find_safest_or_fastest_path(G, src_lat, src_lon, dest_lat, dest_lon, route_type, time_bin)

        if path:
            coords = [(G.nodes[n]['y'], G.nodes[n]['x']) for n in path]
            print("🛣️ Route Info:", info)
            return jsonify({"status":"success","route": coords, "info": info})
        else:
            print("Error info:", info)
            return jsonify({"status":"error","error": info.get("error", "Unknown error")}), 400

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status":"error","message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
