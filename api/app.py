from flask import Flask, request, jsonify
from flask_cors import CORS
import osmnx as ox
import networkx as nx
import pandas as pd
import numpy as np
import joblib
from datetime import datetime
from xai_explainer import RouteExplainer

app = Flask(__name__)
CORS(app)

# ── Load everything ONCE at startup ──────────────────────────────────────────
print("Loading graph...")
G_proj = ox.load_graphml("los_angeles_graph.graphml")
G      = ox.project_graph(G_proj, to_crs="EPSG:4326")

print("Loading ML models...")
edges_ml     = pd.read_pickle("edge_features.pkl")
pipeline     = joblib.load("road_risk_model.pkl")
feature_cols = joblib.load("feature_columns.pkl")

HOUR_COLS   = [f"crime_hour_{h}" for h in range(24)]
HOUR_MATRIX = edges_ml[HOUR_COLS].values

_risk_cache            = {}
_travel_times_assigned = False

print("Loading XAI explainer...")
explainer = RouteExplainer(pipeline, feature_cols, edges_ml, graph=G_proj)

print("✅ All assets loaded")

# ── Speed map ─────────────────────────────────────────────────────────────────
SPEED_MAP = {
    "motorway": 100, "trunk": 80, "primary": 60,
    "secondary": 50, "tertiary": 40, "residential": 30,
    "unclassified": 30, "service": 20, "living_street": 10,
}

def get_speed(highway):
    if isinstance(highway, list):
        highway = highway[0]
    return SPEED_MAP.get(highway, 30)

def assign_travel_times():
    global _travel_times_assigned
    if _travel_times_assigned:
        return
    for u, v, k, data in G_proj.edges(keys=True, data=True):
        length_m  = data.get("length", 0)
        speed_kph = get_speed(data.get("highway", "residential"))
        if "maxspeed" in data:
            try:
                ms = data["maxspeed"]
                if isinstance(ms, list): ms = ms[0]
                speed_kph = float(str(ms).replace(" mph","").replace(" kph","").strip())
            except (ValueError, AttributeError):
                pass
        speed_mps = speed_kph * 1000 / 3600
        data["travel_time"] = length_m / speed_mps if speed_mps > 0 else 9999
    _travel_times_assigned = True

def compute_edge_risk(hour):
    if hour in _risk_cache:
        risk_lookup = _risk_cache[hour]
    else:
        predict_df = edges_ml.copy()
        predict_df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        predict_df["hour_cos"] = np.cos(2 * np.pi * hour / 24)

        predict_df["crimes_night"]     = HOUR_MATRIX[:, [0,1,2,3,4,5]].sum(axis=1)
        predict_df["crimes_morning"]   = HOUR_MATRIX[:, [6,7,8,9,10,11]].sum(axis=1)
        predict_df["crimes_afternoon"] = HOUR_MATRIX[:, [12,13,14,15,16,17]].sum(axis=1)
        predict_df["crimes_evening"]   = HOUR_MATRIX[:, [18,19,20,21,22,23]].sum(axis=1)

        for col in ["crimes_night","crimes_morning","crimes_afternoon","crimes_evening"]:
            max_val = predict_df[col].max()
            if max_val > 0:
                predict_df[col] /= max_val

        crimes_this_hour = HOUR_MATRIX[:, hour].astype(float)
        max_cth = crimes_this_hour.max()
        predict_df["crimes_this_hour"] = (
            crimes_this_hour / max_cth if max_cth > 0 else crimes_this_hour
        )

        X_pred   = predict_df[feature_cols]
        raw_risk = pipeline.predict(X_pred)

        min_r, max_r = raw_risk.min(), raw_risk.max()
        normalized_risk = (
            (raw_risk - min_r) / (max_r - min_r)
            if max_r > min_r else np.zeros_like(raw_risk)
        )

        risk_lookup = dict(zip(
            zip(predict_df["u"], predict_df["v"]),
            normalized_risk
        ))
        _risk_cache[hour] = risk_lookup

    nx.set_edge_attributes(
        G_proj,
        {(u, v, k): {"ml_risk": risk_lookup.get((u, v), 0)}
         for u, v, k in G_proj.edges(keys=True)}
    )

def make_weight(route_type, alpha=0.5, beta=0.5):
    def weight(u, v, data):
        attr        = data[0] if 0 in data else data
        travel_time = attr.get("travel_time", 1)
        risk        = attr.get("ml_risk", 0)
        if route_type == "fastest":
            return travel_time
        elif route_type == "safest":
            return risk + 1e-6
        elif route_type == "balanced":
            return alpha * (travel_time / 3600) + beta * risk
    return weight

def _get_edge_attr(u, v, attr_name, default=0):
    edge_data = G_proj.get_edge_data(u, v)
    attr = edge_data[0] if edge_data and 0 in edge_data else (edge_data or {})
    return attr.get(attr_name, default)

def path_travel_time_minutes(path):
    return round(
        sum(_get_edge_attr(u, v, "travel_time")
            for u, v in zip(path[:-1], path[1:])) / 60, 1
    )

def path_distance_km(path):
    return round(
        sum(_get_edge_attr(u, v, "length")
            for u, v in zip(path[:-1], path[1:])) / 1000, 2
    )

def path_avg_risk(path):
    risks = [_get_edge_attr(u, v, "ml_risk")
             for u, v in zip(path[:-1], path[1:])]
    return round(float(np.mean(risks)), 4) if risks else 0

def path_to_coords(path):
    """Convert node path → list of [lat, lon] for Leaflet Polyline."""
    coords = []
    for node in path:
        node_data = G.nodes[node]
        coords.append([node_data["y"], node_data["x"]])
    return coords

def risk_to_safety(avg_risk):
    safety_score = round(10 * (1 - avg_risk), 1)
    if avg_risk < 0.2:
        safety_level = "Low Risk"
    elif avg_risk < 0.4:
        safety_level = "Moderate Risk"
    elif avg_risk < 0.6:
        safety_level = "High Risk"
    else:
        safety_level = "Very High Risk"
    return safety_level, safety_score

def parse_hour_from_time(time_str):
    """
    Frontend sends getCurrentFormattedTime() — handle common formats:
    '14:30', '2:30 PM', '14:30:00'
    Falls back to current hour if parsing fails.
    """
    try:
        for fmt in ["%H:%M", "%I:%M %p", "%H:%M:%S", "%I:%M:%S %p"]:
            try:
                return datetime.strptime(time_str.strip(), fmt).hour
            except ValueError:
                continue
    except Exception:
        pass
    return datetime.now().hour

def _parse_coords(source_str, destination_str):
    """Shared coord parsing used by all route endpoints."""
    source_lat, source_lon = map(float, source_str.split(","))
    dest_lat,   dest_lon   = map(float, destination_str.split(","))
    return source_lat, source_lon, dest_lat, dest_lon


# ── Main route endpoint ───────────────────────────────────────────────────────
@app.route("/find_safe_route", methods=["GET"])
def find_safe_route():
    """
    Find a route between two locations.

    Query params:
        source       str  — "lat,lon"
        destination  str  — "lat,lon"
        time         str  — "HH:MM" (optional, defaults to current time)
        route_type   str  — "safest" | "fastest" | "safest_fastest" (balanced)

    Response:
    {
      "route": [[lat, lon], ...],
      "info": {
        "distance"    : "30.77 km",
        "duration"    : "41.9 min",
        "safety_level": "Moderate Risk",
        "safety_score": 7.5
      }
    }
    """
    try:
        source_str      = request.args.get("source", "")
        destination_str = request.args.get("destination", "")
        time_str        = request.args.get("time", "")
        route_type      = request.args.get("route_type", "balanced")

        if not source_str or not destination_str:
            return jsonify({"error": "Missing source or destination parameter"}), 400

        source_lat, source_lon, dest_lat, dest_lon = _parse_coords(source_str, destination_str)
        hour          = parse_hour_from_time(time_str) if time_str else datetime.now().hour
        TYPE_MAP      = {"safest": "safest", "fastest": "fastest", "safest_fastest": "balanced"}
        internal_type = TYPE_MAP.get(route_type, "balanced")

        assign_travel_times()
        compute_edge_risk(hour)

        start_node = ox.distance.nearest_nodes(G, source_lon, source_lat)
        end_node   = ox.distance.nearest_nodes(G, dest_lon,   dest_lat)

        if start_node == end_node:
            return jsonify({
                "error": "Start and destination map to the same node — try locations further apart."
            }), 400

        path = nx.dijkstra_path(
            G_proj, start_node, end_node,
            weight=make_weight(internal_type)
        )

        avg_risk               = path_avg_risk(path)
        safety_level, safety_score = risk_to_safety(avg_risk)

        return jsonify({
            "route": path_to_coords(path),
            "info": {
                "distance"    : f"{path_distance_km(path)} km",
                "duration"    : f"{path_travel_time_minutes(path)} min",
                "safety_level": safety_level,
                "safety_score": safety_score,
            }
        })

    except nx.NetworkXNoPath:
        return jsonify({"error": "No path found between these locations."}), 404
    except ValueError as e:
        return jsonify({"error": f"Invalid coordinates format: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── XAI: explain a route ──────────────────────────────────────────────────────
@app.route("/explain_route", methods=["GET"])
def explain_route():
    """
    Returns a user-friendly risk explanation for a given route.

    Same query params as /find_safe_route:
        source, destination, route_type, time

    Response:
    {
      "explanation": {
        // ── User-facing (shown in the UI) ──────────────────────────────
        "overview_message"  : str,   // 2-sentence conversational summary
        "caution_message"   : str,   // what the user should do / feel
        "main_risk_factor"  : str,   // plain-English name of the #1 driver
        "main_risk_context" : str,   // one sentence explaining what it means
        "risk_distribution" : {
          "low": float, "moderate": float, "high": float, "very_high": float,
          "user_breakdown": str      // e.g. "3% calm, 68% moderate care, 29% stay alert"
        },
        "segments_to_watch" : [      // up to 3 stretches worth highlighting
          {
            "u": int, "v": int,
            "caution_level": str,    // "Calm" / "Moderate caution" / "Stay alert" / "High caution"
            "plain_reason" : str,    // e.g. "Higher than usual crime reported here at night."
            "risk"         : float   // raw score for frontend colour-coding
          }
        ],
        // ── Debug / internal (not shown to users) ──────────────────────
        "avg_risk"        : float,
        "risk_level"      : str,
        "total_edges"     : int,
        "riskiest_segments": [...]   // includes shap values
      }
    }
    """
    try:
        source_str      = request.args.get("source", "")
        destination_str = request.args.get("destination", "")
        time_str        = request.args.get("time", "")
        route_type      = request.args.get("route_type", "safest")

        if not source_str or not destination_str:
            return jsonify({"error": "Missing source or destination"}), 400

        source_lat, source_lon, dest_lat, dest_lon = _parse_coords(source_str, destination_str)
        hour          = parse_hour_from_time(time_str) if time_str else datetime.now().hour
        TYPE_MAP      = {"safest": "safest", "fastest": "fastest", "safest_fastest": "balanced"}
        internal_type = TYPE_MAP.get(route_type, "balanced")

        assign_travel_times()
        compute_edge_risk(hour)

        start_node = ox.distance.nearest_nodes(G, source_lon, source_lat)
        end_node   = ox.distance.nearest_nodes(G, dest_lon,   dest_lat)

        path        = nx.dijkstra_path(G_proj, start_node, end_node,
                                       weight=make_weight(internal_type))
        explanation = explainer.explain_route(path, hour)

        return jsonify({"explanation": explanation})

    except nx.NetworkXNoPath:
        return jsonify({"error": "No path found between these locations"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── XAI: compare safest vs fastest ───────────────────────────────────────────
@app.route("/compare_routes", methods=["GET"])
def compare_routes():
    """
    Computes both safest and fastest routes, then explains the trade-off
    in plain English for the user.

    Query params:
        source, destination, time

    Response:
    {
      "comparison": {
        // ── User-facing (shown in the UI) ──────────────────────────────
        "summary"            : str,    // 2-sentence conversational summary
        "tradeoff_message"   : str,    // "X extra minutes for Y% calmer route"
        "safest_pct"         : int,    // e.g. 31  (shown in the Safest card)
        "fastest_pct"        : int,    // e.g. 35  (shown in the Fastest card)
        "risk_reduction_pct" : float,  // e.g. 12.5
        "user_differences"   : [str],  // max 4 plain-English bullet points
        // travel cards
        "safest_travel_min"  : float,
        "fastest_travel_min" : float,
        "safest_distance_km" : float,
        "fastest_distance_km": float,
        // ── Debug / internal (not shown to users) ──────────────────────
        "safest_avg_risk"    : float,
        "fastest_avg_risk"   : float,
        "risk_reduction"     : float,
        "key_differences"    : [...]   // all feature diffs with raw values
      }
    }
    """
    try:
        source_str      = request.args.get("source", "")
        destination_str = request.args.get("destination", "")
        time_str        = request.args.get("time", "")

        if not source_str or not destination_str:
            return jsonify({"error": "Missing source or destination"}), 400

        source_lat, source_lon, dest_lat, dest_lon = _parse_coords(source_str, destination_str)
        hour = parse_hour_from_time(time_str) if time_str else datetime.now().hour

        assign_travel_times()
        compute_edge_risk(hour)

        start_node = ox.distance.nearest_nodes(G, source_lon, source_lat)
        end_node   = ox.distance.nearest_nodes(G, dest_lon,   dest_lat)

        path_safest  = nx.dijkstra_path(G_proj, start_node, end_node,
                                        weight=make_weight("safest"))
        path_fastest = nx.dijkstra_path(G_proj, start_node, end_node,
                                        weight=make_weight("fastest"))

        comparison = explainer.compare_routes(
            path_safest, path_fastest, hour,
            time_safest_min=path_travel_time_minutes(path_safest),
            time_fastest_min=path_travel_time_minutes(path_fastest),
            dist_safest_km=path_distance_km(path_safest),
            dist_fastest_km=path_distance_km(path_fastest),
        )

        return jsonify({"comparison": comparison})

    except nx.NetworkXNoPath:
        return jsonify({"error": "No path found between these locations"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── XAI: explain a single road segment ───────────────────────────────────────
@app.route("/explain_edge", methods=["GET"])
def explain_edge():
    """
    Explains why a specific road segment has its risk score.
    Designed for clickable map segments.

    Query params:
        u     int  — OSM node ID
        v     int  — OSM node ID
        time  str  — HH:MM (optional)

    Response:
    {
      "explanation": {
        // ── User-facing ──────────────────────────────────────────────
        "caution_level" : str,   // "Calm" / "Moderate caution" / "Stay alert" / "High caution"
        "plain_reason"  : str,   // e.g. "Higher than usual crime here at night."
        "summary"       : str,   // caution_level + plain_reason combined
        // ── Debug / internal ─────────────────────────────────────────
        "edge"             : {"u": int, "v": int},
        "hour"             : int,
        "predicted_risk"   : float,
        "base_value"       : float,
        "top_contributors" : [...]
      }
    }
    """
    try:
        u        = int(request.args.get("u"))
        v        = int(request.args.get("v"))
        time_str = request.args.get("time", "")
        hour     = parse_hour_from_time(time_str) if time_str else datetime.now().hour

        explanation = explainer.explain_edge(u, v, hour)
        return jsonify({"explanation": explanation})

    except (TypeError, ValueError):
        return jsonify({"error": "u and v must be integers"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Health check ──────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status"      : "ok",
        "graph_nodes" : G_proj.number_of_nodes(),
        "graph_edges" : G_proj.number_of_edges(),
        "cache_hours" : list(_risk_cache.keys()),
    })


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)