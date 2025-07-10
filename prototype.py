import networkx as nx
import osmnx as ox
import matplotlib.pyplot as plt
from neo4j import GraphDatabase, basic_auth
import os
from shapely.geometry import LineString
from itertools import combinations
import math

# --- Neo4j AuraDB Connection Details ---
NEO4J_URI = "neo4j+s://c19f8aa3.databases.neo4j.io"
NEO4J_USERNAME = "neo4j"
NEO4J_PASSWORD = "okH3VVp0qJRCkmdQ70FhBqyNxVJi3Mx3bk4btNocnSw" # Your actual password

# --- Connect to Neo4j ---
try:
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=basic_auth(NEO4J_USERNAME, NEO4J_PASSWORD),
        connection_timeout=60 # Increased timeout
    )
    driver.verify_connectivity()
    print("Successfully connected to Neo4j AuraDB!")
except Exception as e:
    print(f"Failed to connect to Neo4j AuraDB: {e}")
    print("Please ensure your AuraDB instance is running and connection details are correct.")
    exit()

# --- Helper Functions (from your original code) ---
available_time_bins = ["00:00:00", "03:00:00", "06:00:00","09:00:00", "12:00:00",
                       "15:00:00","18:00:00", "21:00:00"]

def get_time_bin(user_time, available_bins):
    user_hour = int(user_time.split(":")[0])
    selected_bin = available_bins[0]
    for bin_time in sorted(available_bins):
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

# --- New Function: Find Safest/Fastest Route by fetching subgraph from Neo4j ---
def find_route_from_neo4j_data(source_lat, source_lon, dest_lat, dest_lon, time_bin, route_type, buffer_degrees=0.06):
    """
    Fetches a subgraph from Neo4j, builds a local NetworkX graph,
    and then finds the safest/fastest route using NetworkX's A* algorithm.

    Args:
        source_lat (float), source_lon (float): Latitude and longitude of the starting point.
        dest_lat (float), dest_lon (float): Latitude and longitude of the destination point.
        time_bin (str): The selected time bin (e.g., "19:00:00").
        route_type (str): "safest", "fastest", or "safest_fastest".
        buffer_degrees (float): Degrees to expand the bounding box around source/dest points.
                                Adjust this based on your graph density and expected route lengths.

    Returns:
        tuple: (local_graph_for_path, path_osmid_list, chosen_time_bin, route_info)
               Returns (None, None, None, {"error": "..."}) on failure.
    """
    # Important: Confirm the actual property name in Neo4j.
    # If your properties use underscores (e.g., severity_19_00_00), uncomment and use this:
    # severity_attr = f"severity_{time_bin.replace(':', '_')}"
    # Otherwise, keep it as is if properties are exactly "severity_HH:MM:SS" (requiring backticks in Cypher)
    severity_attr = f"severity_{time_bin}" 
    
    # 1. Define a bounding box to fetch relevant data
    min_lat = min(source_lat, dest_lat) - buffer_degrees
    max_lat = max(source_lat, dest_lat) + buffer_degrees
    min_lon = min(source_lon, dest_lon) - buffer_degrees
    max_lon = max(source_lon, dest_lon) + buffer_degrees

    print(f"Fetching subgraph from Neo4j within bounding box: "
          f"lat ({min_lat:.4f}-{max_lat:.4f}), lon ({min_lon:.4f}-{max_lon:.4f})")

    local_G = nx.MultiDiGraph() # This will be our local NetworkX graph
    
    local_G.graph["crs"] = "epsg:4326" 
    local_G.graph["name"] = "Subgraph from Neo4j"
    local_G.graph["bbox"] = (max_lat, min_lat, max_lon, min_lon) # lat_north, lat_south, lon_east, lon_west

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

            # Add fetched nodes to the local NetworkX graph
            node_osmid_to_id_map = {} 
            for record in nodes_result:
                osmid = record['osmid']
                lat = record['latitude']
                lon = record['longitude']
                local_G.add_node(osmid, x=lon, y=lat, osmid=osmid) 
                node_osmid_to_id_map[osmid] = osmid 

            if not local_G.nodes:
                return None, None, None, {"error": "No nodes found in the specified bounding box. Try increasing buffer_degrees."}

            print(f"Fetched {len(local_G.nodes)} nodes from Neo4j.")

            # --- Fetch Edges within Bounding Box (between the fetched nodes) ---
            edges_query = f"""
            MATCH (u:Intersection)-[r:ROAD_SEGMENT]->(v:Intersection)
            WHERE u.osmid IN $osmids AND v.osmid IN $osmids
            RETURN
                u.osmid AS u_osmid, v.osmid AS v_osmid, r.key AS key,
                r.length AS length, r.`{severity_attr}` AS severity_val,
                r.geometry_coords AS geometry_coords
            """
            osmids_in_bbox = list(node_osmid_to_id_map.keys())
            edges_result = session.run(edges_query, osmids=osmids_in_bbox).data()

            # Add fetched edges to the local NetworkX graph
            for record in edges_result:
                u_osmid = record['u_osmid']
                v_osmid = record['v_osmid']
                key = record['key']
                
                # --- FIX: Convert length and severity to float ---
                # Default to 0.0 if None, then cast to float.
                # If length might also be missing, provide a default like 0.0 or float('inf')
                length = float(record['length']) if record['length'] is not None else 0.0 
                
                # Default to 0.0 (safest) if severity_val is None (property not found)
                # Then cast to float. This handles the 'UnknownPropertyKeyWarning'.
                severity = float(record['severity_val']) if record['severity_val'] is not None else 0.0 
                
                geometry_coords_flat = record['geometry_coords']

                # Reconstruct LineString geometry for OSMnx plotting
                geometry_obj = None
                if geometry_coords_flat and len(geometry_coords_flat) % 2 == 0:
                    coords_tuples = [(geometry_coords_flat[i], geometry_coords_flat[i+1])
                                     for i in range(0, len(geometry_coords_flat), 2)]
                    geometry_obj = LineString(coords_tuples)

                local_G.add_edge(u_osmid, v_osmid, key=key,
                                 length=length,
                                 **{severity_attr: severity}, # Dynamically set severity attribute
                                 geometry=geometry_obj)
            
            if not local_G.edges:
                 return None, None, None, {"error": "No edges found connecting nodes in the specified bounding box. Try increasing buffer_degrees."}

            print(f"Fetched {len(local_G.edges)} edges from Neo4j.")

            # 2. Find nearest nodes in the locally constructed graph
            start_node_osmid = ox.distance.nearest_nodes(local_G, source_lon, source_lat)
            end_node_osmid = ox.distance.nearest_nodes(local_G, dest_lon, dest_lat)

            if start_node_osmid not in local_G or end_node_osmid not in local_G:
                return None, None, None, {"error": "Nearest start/end nodes not found in the fetched subgraph. Try increasing buffer_degrees."}
            
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
                return None, None, None, {"error": "Invalid route_type"}

            # 4. Compute the path using NetworkX A* on the local graph
            print(f"Computing path with route_type='{route_type}' and weight='{weight_for_astar}'...")
            path_osmid_list = nx.astar_path(local_G, start_node_osmid, end_node_osmid, weight=weight_for_astar)
            
            # 5. Calculate route metrics from the found path
            total_distance = 0.0 # Initialize as float
            total_severity = 0.0 # Initialize as float
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
                        # These values are now guaranteed to be floats due to casting above
                        total_distance += best_edge_for_metrics.get("length", 0.0)
                        total_severity += best_edge_for_metrics.get(severity_attr, 0.0)
                        num_edges_in_path += 1
                else:
                    print(f"Warning: No edge data found between {u} and {v} in local graph for metrics. This shouldn't happen if a path was found.")


            avg_severity = total_severity / num_edges_in_path if num_edges_in_path > 0 else 0.0
            estimated_minutes = total_distance / 1000 / 30 * 60  

            route_info = {
                "start_node_osmid": start_node_osmid,
                "dest_node_osmid": end_node_osmid,
                "distance": f"{total_distance / 1000:.2f} km",
                "duration": f"{int(estimated_minutes)} min",
                "safety_level": severity_level(avg_severity),
                "safety_score": round(avg_severity, 2)
            }
            
            return local_G, path_osmid_list, time_bin, route_info

        except nx.NetworkXNoPath:
            return None, None, None, {"error": "No route found between the points in the fetched subgraph. Try increasing buffer_degrees."}
        except Exception as e:
            print(f"An error occurred during route finding: {e}")
            import traceback
            traceback.print_exc()
            return None, None, None, {"error": str(e)}

# --- Main Execution Block ---
if __name__ == "__main__":
    # Get user input time
    user_time = input("Enter time (HH:MM:SS format, e.g., 19:45:00): ").strip()

    # Determine the correct time bin
    time_bin = get_time_bin(user_time, available_time_bins)
    print(f"Using time bin: {time_bin}")

    # Example coordinates (Griffith Observatory to LAX)
    source_lat, source_lon = 34.1184, -118.3004
    dest_lat, dest_lon = 33.9416, -118.4085

    G_local, path_osmid_list, chosen_time_bin, route_info = find_route_from_neo4j_data(
        source_lat, source_lon, dest_lat, dest_lon, time_bin, route_type="safest", buffer_degrees=0.06
    )

    if G_local and path_osmid_list:
        print("\nRoute Information:")
        for key, value in route_info.items():
            print(f"- {key}: {value}")

        print("\nPlotting route...")
        
        fig, ax = ox.plot_graph_route(
            G_local,
            path_osmid_list,
            route_color='blue',
            route_linewidth=4,
            node_size=0, 
            show=False,
            close=False
        )

        ax.scatter([source_lon, dest_lon], [source_lat, dest_lat],
                   c='red', s=100, zorder=5, label='Start/End')

        plt.title(f"Safest Route ({chosen_time_bin}) from Neo4j Data (Local A*)")
        plt.legend()
        plt.show()
    else:
        print(f"Could not find or plot the route. Error: {route_info.get('error', 'Unknown error.')}")

    # Close the Neo4j driver when done
    if 'driver' in locals() and driver:
        driver.close()
        print("Neo4j driver closed.")