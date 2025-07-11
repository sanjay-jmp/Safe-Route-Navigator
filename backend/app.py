from flask import Flask, request, jsonify
from flask_cors import CORS
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString
from neo4j import GraphDatabase, basic_auth
import math
import os
import functools # Import functools for lru_cache

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": 'https://lively-youtiao-bace0b.netlify.app'}}) 

# --- Neo4j AuraDB Connection Details ---
#It's highly recommended to use environment variables for credentials in production
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j+s://c19f8aa3.databases.neo4j.io")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "okH3VVp0qJRCkmdQ70FhBqyNxVJi3Mx3bk4btNocnSw")


# --- Global Neo4j Driver (initialized once on app startup) ---
driver = None
try:
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=basic_auth(NEO4J_USERNAME, NEO4J_PASSWORD),
        connection_timeout=60 # Increased timeout for initial connection
    )
    driver.verify_connectivity()
    print("Successfully connected to Neo4j AuraDB!")
except Exception as e:
    print(f"Failed to connect to Neo4j AuraDB on startup: {e}")
    print("Please ensure your AuraDB instance is running and connection details are correct.")
    # In a production app, you might want to exit or log a critical error
    # For now, we'll let the app run but route finding will fail.

# --- Available Time Bins ---
available_time_bins = ["00:00:00", "03:00:00", "06:00:00", "09:00:00",
                       "12:00:00", "15:00:00", "18:00:00", "21:00:00"]


# --- Helper Functions ---
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

# --- Cached Function to Fetch and Build Subgraph ---
@functools.lru_cache(maxsize=128) # Cache up to 128 unique subgraphs
def _get_cached_subgraph(min_lat, max_lat, min_lon, max_lon, time_bin):
    """
    Fetches graph data from Neo4j within a bounding box and constructs a local NetworkX graph.
    This function is cached to avoid repeated Neo4j queries and graph construction for identical requests.
    """
    if driver is None:
        raise Exception("Neo4j driver not initialized.")

    severity_attr = f"severity_{time_bin}" 

    local_G = nx.MultiDiGraph()
    local_G.graph["crs"] = "epsg:4326" 
    local_G.graph["name"] = "Subgraph from Neo4j"
    local_G.graph["bbox"] = (max_lat, min_lat, max_lon, min_lon) 

    with driver.session() as session:
        try:
            # --- Fetch Nodes within Bounding Box ---
            nodes_query = """
            MATCH (n:Intersection)
            WHERE n.latitude >= $min_lat AND n.latitude <= $max_lat
              AND n.longitude >= $min_lon AND n.longitude <= $max_lon
            RETURN n.osmid AS osmid, n.latitude AS latitude, n.longitude AS longitude
            """
            nodes_result = session.run(nodes_query, min_lat=min_lat, max_lat=max_lat,
                                       min_lon=min_lon, max_lon=max_lon).data()

            if not nodes_result:
                print(f"No nodes found in bounding box: lat ({min_lat:.4f}-{max_lat:.4f}), lon ({min_lon:.4f}-{max_lon:.4f})")
                return None # Indicate no data fetched

            for record in nodes_result:
                osmid = record['osmid']
                lat = record['latitude']
                lon = record['longitude']
                local_G.add_node(osmid, x=lon, y=lat, osmid=osmid) 

            # --- Fetch Edges within Bounding Box (between the fetched nodes) ---
            osmids_in_bbox = list(local_G.nodes())
            if not osmids_in_bbox: # Should be covered by nodes_result check, but as a safeguard
                print("No OSMIDs in bbox to query edges.")
                return None

            edges_query = f"""
            MATCH (u:Intersection)-[r:ROAD_SEGMENT]->(v:Intersection)
            WHERE u.osmid IN $osmids AND v.osmid IN $osmids
            RETURN
                u.osmid AS u_osmid, v.osmid AS v_osmid, r.key AS key,
                r.length AS length, r.`{severity_attr}` AS severity_val,
                r.geometry_coords AS geometry_coords
            """
            edges_result = session.run(edges_query, osmids=osmids_in_bbox).data()

            if not edges_result:
                print(f"No edges found connecting nodes in bounding box: lat ({min_lat:.4f}-{max_lat:.4f}), lon ({min_lon:.4f}-{max_lon:.4f})")
                return None # Indicate no data fetched

            for record in edges_result:
                u_osmid = record['u_osmid']
                v_osmid = record['v_osmid']
                key = record['key']
                
                length = float(record['length']) if record['length'] is not None else 0.0 
                severity = float(record['severity_val']) if record['severity_val'] is not None else 0.0 
                
                geometry_coords_flat = record['geometry_coords']
                geometry_obj = None
                if geometry_coords_flat and len(geometry_coords_flat) % 2 == 0:
                    coords_tuples = [(geometry_coords_flat[i], geometry_coords_flat[i+1])
                                     for i in range(0, len(geometry_coords_flat), 2)]
                    geometry_obj = LineString(coords_tuples)

                local_G.add_edge(u_osmid, v_osmid, key=key,
                                 length=length,
                                 **{severity_attr: severity}, 
                                 geometry=geometry_obj)
            
            print(f"Fetched and constructed subgraph with {len(local_G.nodes)} nodes and {len(local_G.edges)} edges.")
            return local_G

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error fetching subgraph: {e}")
            return None # Return None on error, cache won't store this result

# --- Core Logic: Find Route by utilizing cached subgraph ---
def _find_route_from_neo4j_data(source_lat, source_lon, dest_lat, dest_lon, time_bin, route_type, buffer_degrees=0.06):
    """
    Finds the safest/fastest route using NetworkX's A* algorithm on a (potentially cached) subgraph.
    """
    # 1. Define a bounding box to identify the relevant subgraph
    min_lat = min(source_lat, dest_lat) - buffer_degrees
    max_lat = max(source_lat, dest_lat) + buffer_degrees
    min_lon = min(source_lon, dest_lon) - buffer_degrees
    max_lon = max(source_lon, dest_lon) + buffer_degrees

    # Get the cached or newly fetched subgraph
    # The cache key for _get_cached_subgraph needs to be hashable, so ensure floats are used directly.
    local_G = _get_cached_subgraph(min_lat, max_lat, min_lon, max_lon, time_bin)

    if local_G is None:
        return None, None, {"error": "No graph data available for the specified region and time. Try increasing buffer_degrees."}
    
    severity_attr = f"severity_{time_bin}" # Define for this scope

    try:
        # 2. Find nearest nodes in the locally constructed graph
        start_node_osmid = ox.distance.nearest_nodes(local_G, source_lon, source_lat)
        end_node_osmid = ox.distance.nearest_nodes(local_G, dest_lon, dest_lat)

        if start_node_osmid not in local_G or end_node_osmid not in local_G:
            return None, None, {"error": "Nearest start/end nodes not found in the fetched subgraph. Try increasing buffer_degrees."}
        
        print(f"Nearest start node OSMID: {start_node_osmid}")
        print(f"Nearest end node OSMID: {end_node_osmid}")

        # 3. Define the weight for A* based on route_type
        weight_for_astar = None
        if route_type == "safest":
            weight_for_astar = severity_attr
        elif route_type == "fastest":
            weight_for_astar = "length"
        elif route_type == "safest_fastest":
            def custom_weight(u, v, data):
                severity = data.get(severity_attr, float('inf')) 
                length = data.get("length", float('inf'))
                
                if math.isinf(severity) or math.isinf(length):
                     return float('inf') 
                
                return 0.5 * severity + 0.5 * length
            weight_for_astar = custom_weight
        else:
            return None, None, {"error": "Invalid route_type"}

        # 4. Compute the path using NetworkX A* on the local graph
        print(f"Computing path with route_type='{route_type}' and weight='{weight_for_astar}'...")
        path_osmid_list = nx.astar_path(local_G, start_node_osmid, end_node_osmid, weight=weight_for_astar)
        
        # 5. Calculate route metrics from the found path
        total_distance = 0.0 
        total_severity = 0.0 
        num_edges_in_path = 0

        for i in range(len(path_osmid_list) - 1):
            u = path_osmid_list[i]
            v = path_osmid_list[i+1]
            
            edge_data_dict = local_G.get_edge_data(u, v)
            
            if edge_data_dict:
                best_edge_for_metrics = None
                min_cost = float('inf')
                
                for key_val, data in edge_data_dict.items():
                    current_cost = 0
                    if isinstance(weight_for_astar, str): 
                        current_cost = data.get(weight_for_astar, float('inf'))
                    elif callable(weight_for_astar): 
                        current_cost = weight_for_astar(u, v, data)
                    
                    if current_cost < min_cost:
                        min_cost = current_cost
                        best_edge_for_metrics = data

                if best_edge_for_metrics:
                    total_distance += best_edge_for_metrics.get("length", 0.0)
                    total_severity += best_edge_for_metrics.get(severity_attr, 0.0)
                    num_edges_in_path += 1
            else:
                # This warning should ideally not happen if a path was successfully found
                print(f"Warning: No edge data found between {u} and {v} in local graph for metrics. This might indicate a problem.")


        avg_severity = total_severity / num_edges_in_path if num_edges_in_path > 0 else 0.0
        estimated_minutes = total_distance / 1000 / 30 * 60  

        route_info = {
            "start_node": start_node_osmid, 
            "dest_node": end_node_osmid,     
            "distance": f"{total_distance / 1000:.2f} km",
            "duration": f"{int(estimated_minutes)} min",
            "safety_level": severity_level(avg_severity),
            "safety_score": round(avg_severity, 2)
        }
        
        return local_G, path_osmid_list, route_info

    except nx.NetworkXNoPath:
        return None, None, {"error": "No route found between the points in the fetched subgraph. The area might be disconnected or buffer_degrees too small."}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, None, {"error": f"An internal error occurred during path computation: {e}"}

@app.route('/')
def home():
    """
    Simple home endpoint to check if the backend is running.
    """
    return jsonify({
        "status": "success",
        "message": "Safe Route Navigator Backend is running!",
        "version": "1.0.0" # Optional: add a version
    }), 200 # HTTP 200 OK status code

@app.route('/find_safe_route', methods=['GET'])
def get_safe_route():
    source = request.args.get('source')  # "lat,lon"
    destination = request.args.get('destination')  # "lat,lon"
    user_time = request.args.get('time')  # "HH:MM:SS"
    route_type = request.args.get('route_type', 'safest')

    if not source or not destination or not user_time:
        return jsonify({"error": "Missing required parameters (source, destination, time)."}), 400

    try:
        src_lat, src_lon = map(float, source.split(','))
        dest_lat, dest_lon = map(float, destination.split(','))
    except ValueError:
        return jsonify({"error": "Invalid coordinates format. Use 'lat,lon'."}), 400

    time_bin = get_time_bin(user_time)

    local_G, path_osmid_list, route_info = _find_route_from_neo4j_data(
        src_lat, src_lon, dest_lat, dest_lon, time_bin, route_type, buffer_degrees=0.06
    )

    if local_G and path_osmid_list:
        route_coords = []
        for i in range(len(path_osmid_list) - 1):
            u_osmid = path_osmid_list[i]
            v_osmid = path_osmid_list[i+1]
            
            edge_data_dict = local_G.get_edge_data(u_osmid, v_osmid)
            best_edge_for_geometry = next(iter(edge_data_dict.values()), None)

            if best_edge_for_geometry and 'geometry' in best_edge_for_geometry:
                coords = list(best_edge_for_geometry['geometry'].coords)
                route_coords.extend([(lat, lon) for lon, lat in coords])
            else:
                if not route_coords or route_coords[-1] != (local_G.nodes[u_osmid]['y'], local_G.nodes[u_osmid]['x']):
                    route_coords.append((local_G.nodes[u_osmid]['y'], local_G.nodes[u_osmid]['x']))
                route_coords.append((local_G.nodes[v_osmid]['y'], local_G.nodes[v_osmid]['x']))

        return jsonify({
            "route": route_coords,
            "info": route_info
        })
    else:
        error_message = str(e)
        app.logger.error(f"Error in find_safe_route: {error_message}")
        return jsonify({
            "status": "error",
            "message": "An internal server error occurred while fetching the route.",
            "details": error_message # Include the actual error message here
            # You might also include route_info here if it contains partial valid data on error
        }), 500

if __name__ == '__main__':
    app.run(debug=True)

# # --- App Teardown (to gracefully close Neo4j driver) ---
# @app.teardown_appcontext
# def close_neo4j_driver(exception):
#     global driver
#     if driver:
#         driver.close()
#         print("Neo4j driver closed during app teardown.")
#         driver = None # Clear the driver for clean state
#     # Clear the lru_cache for _get_cached_subgraph on teardown if needed for testing scenarios
#     _get_cached_subgraph.cache_clear()
#     print("Subgraph cache cleared.")