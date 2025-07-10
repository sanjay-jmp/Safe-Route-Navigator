import osmnx as ox
from neo4j import GraphDatabase, basic_auth
import os
from tqdm import tqdm # For a nice progress bar

# --- Neo4j AuraDB Connection Details ---
# Replace with your actual details from Step 1
NEO4J_URI = "neo4j+s://c19f8aa3.databases.neo4j.io" # YOUR URI (already updated from previous run)
NEO4J_USERNAME = "neo4j"
NEO4J_PASSWORD = "okH3VVp0qJRCkmdQ70FhBqyNxVJi3Mx3bk4btNocnSw" # Use the password you downloaded

# Path to your local GraphML file
LOCAL_GRAPHML_FILE = "los_angeles_precomputed_severity.graphml"

# --- Batch Size Configuration ---
BATCH_SIZE = 2000 # Number of relationships to import per transaction

# --- Connect to Neo4j ---
driver = GraphDatabase.driver(NEO4J_URI, auth=basic_auth(NEO4J_USERNAME, NEO4J_PASSWORD))

def import_graph_data():
    try:
        # 1. Load the graph locally using OSMnx
        print(f"Loading graph from local file: {LOCAL_GRAPHML_FILE}...")
        G = ox.load_graphml(LOCAL_GRAPHML_FILE)
        print("Graph loaded successfully locally.")

        with driver.session() as session:
            # 2. Clear existing data (OPTIONAL, for fresh imports)
            print("Clearing existing data in Neo4j...")
            session.run("MATCH (n) DETACH DELETE n")
            print("Data cleared.")

            # 3. Import Nodes
            print("Importing nodes to Neo4j...")
            node_data = []
            for node_id, data in G.nodes(data=True):
                props = {k: v for k, v in data.items()}
                props['osmid'] = node_id
                props['label'] = 'Intersection'

                if 'x' in props and 'y' in props:
                    props['longitude'] = props.pop('x')
                    props['latitude'] = props.pop('y')
                else:
                    continue
                node_data.append(props)

            query_nodes = """
            UNWIND $nodes AS node
            CREATE (n:Intersection {osmid: node.osmid, latitude: node.latitude, longitude: node.longitude})
            SET n += node
            """
            session.run(query_nodes, nodes=node_data)
            print(f"Imported {len(node_data)} nodes.")

            # 4. Import Relationships (Edges) - BATCHED
            print(f"Importing relationships to Neo4j in batches of {BATCH_SIZE}...")
            rel_data_all = []
            for u, v, key, data in G.edges(keys=True, data=True):
                props = {k: v for k, v in data.items()}
                props['osmid_u'] = u
                props['osmid_v'] = v
                props['key'] = key
                props['type'] = 'ROAD_SEGMENT'

                # --- FIX FOR COLLECTIONS IN COLLECTIONS ERROR ---
                if 'geometry' in props:
                    flattened_coords = []
                    for coord_pair in props['geometry'].coords:
                        flattened_coords.extend(coord_pair)
                    props['geometry_coords'] = flattened_coords
                    del props['geometry']
                # --- END FIX ---

                rel_data_all.append(props)

            total_relationships = len(rel_data_all)
            for i in tqdm(range(0, total_relationships, BATCH_SIZE), desc="Importing relationships"):
                batch = rel_data_all[i:i + BATCH_SIZE]
                query_relationships = """
                UNWIND $relationships AS rel
                MATCH (u:Intersection {osmid: rel.osmid_u})
                MATCH (v:Intersection {osmid: rel.osmid_v})
                CREATE (u)-[r:ROAD_SEGMENT]->(v)
                SET r += rel
                """
                try:
                    session.run(query_relationships, relationships=batch)
                except Exception as batch_e:
                    print(f"\nError importing batch {i}-{i+BATCH_SIZE}: {batch_e}")
                    raise


            print(f"Imported {total_relationships} relationships.")

            # 5. Create Indexes (Crucial for performance) - After all data is in
            print("Creating indexes...")
            # --- FIX FOR INDEX SYNTAX ---
            session.run("CREATE INDEX FOR (n:Intersection) ON (n.osmid)")
            session.run("CREATE INDEX FOR (n:Intersection) ON (n.latitude, n.longitude)")
            # --- END FIX ---
            print("Indexes created.")

        print("Graph data imported to Neo4j AuraDB successfully!")

    except Exception as e:
        print(f"An error occurred during import: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.close()
        print("Neo4j driver closed.")

if __name__ == "__main__":
    import_graph_data()