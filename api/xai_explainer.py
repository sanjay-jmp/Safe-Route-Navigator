"""
xai_explainer.py
────────────────
Explainable AI for SafeRoute Navigator.

Provides SHAP-based explanations for:
  - Individual edge (road segment) risk scores
  - Full route risk profiles (riskiest segments + what drives them)
  - Route comparisons (why safest differs from fastest)

Usage in app.py:
    from xai_explainer import RouteExplainer
    explainer = RouteExplainer(pipeline, feature_cols, edges_ml, graph=G_proj)

Then call:
    explainer.explain_edge(u, v, hour)
    explainer.explain_route(path, hour)
    explainer.compare_routes(path_safest, path_fastest, hour,
                             time_safest_min, time_fastest_min)
"""

import shap
import numpy as np
import pandas as pd


# ── Internal → human label ────────────────────────────────────────────────────
FEATURE_LABELS = {
    "crimes_this_hour"             : "Crime count at this hour",
    "crime_kde_norm"               : "Overall crime density (area)",
    "local_crime_norm"             : "Nearby crime count",
    "distance_weighted_crime_norm" : "Weighted crime proximity",
    "crimes_evening"               : "Evening crime activity",
    "crimes_night"                 : "Night crime activity",
    "crimes_morning"               : "Morning crime activity",
    "crimes_afternoon"             : "Afternoon crime activity",
    "weekend_crime_fraction"       : "Weekend crime concentration",
    "edge_betweenness"             : "Road centrality (traffic hub)",
    "avg_degree"                   : "Number of nearby intersections",
    "length"                       : "Road segment length",
    "hour_sin"                     : "Time of day (cyclic)",
    "hour_cos"                     : "Time of day (cyclic)",
}

# ── Plain-English interpretation for compare_routes ───────────────────────────
DIFF_INTERPRETATIONS = {
    "crimes_this_hour"             : ("Fewer crimes at this specific hour",
                                      "Fewer crimes at this specific hour"),
    "crime_kde_norm"               : ("Passes through lower overall crime density areas",
                                      "Passes through lower overall crime density areas"),
    "local_crime_norm"             : ("Fewer nearby crime incidents along the way",
                                      "Fewer nearby crime incidents along the way"),
    "distance_weighted_crime_norm" : ("Keeps more distance from crime hotspots",
                                      "Keeps more distance from crime hotspots"),
    "crimes_evening"               : ("Less evening crime activity on this route",
                                      "Less evening crime activity on this route"),
    "crimes_night"                 : ("Less night-time crime activity",
                                      "Less night-time crime activity"),
    "crimes_morning"               : ("Less morning crime activity",
                                      "Less morning crime activity"),
    "crimes_afternoon"             : ("Less afternoon crime activity",
                                      "Less afternoon crime activity"),
    "weekend_crime_fraction"       : ("Avoids weekend crime hotspots",
                                      "Avoids weekend crime hotspots"),
    "edge_betweenness"             : ("Uses quieter, less-central roads",
                                      "Uses quieter, less-central roads"),
    "avg_degree"                   : ("More intersections — better visibility & escape routes",
                                      "More intersections — better visibility & escape routes"),
    "length"                       : ("Shorter individual road segments — less exposure at once",
                                      "Shorter individual road segments — less exposure at once"),
}


class RouteExplainer:
    """
    SHAP-based explainer for edge risk scores and route comparisons.
    Initialise once at app startup — SHAP TreeExplainer is expensive to build.
    """

    def __init__(self, pipeline, feature_cols, edges_ml, graph=None):
        self.pipeline     = pipeline
        self.feature_cols = feature_cols
        self.edges_ml     = edges_ml
        self.graph        = graph          # OSMnx projected graph — used for street names
        self.preprocessor = pipeline.named_steps["preprocessor"]
        self.xgb_model    = pipeline.named_steps["model"]

        self._hour_cols   = [f"crime_hour_{h}" for h in range(24)]
        self._hour_matrix = edges_ml[self._hour_cols].values

        self.shap_explainer = shap.TreeExplainer(self.xgb_model)

        ohe_names = list(
            self.preprocessor
            .named_transformers_["cat"]
            .get_feature_names_out(["highway"])
        )
        num_names = [f for f in feature_cols if f != "highway"]
        self.feature_names = ohe_names + num_names

    # ── Street name resolver ──────────────────────────────────────────────────

    def _street_name(self, u: int, v: int) -> str | None:
        """Return a human street name for edge (u, v), or None."""
        if self.graph is None:
            return None
        edge_data = self.graph.get_edge_data(u, v)
        if not edge_data:
            return None
        attr = edge_data[0] if 0 in edge_data else edge_data
        name = attr.get("name")
        if isinstance(name, list):
            name = name[0]
        if name:
            return str(name)
        # Fall back to road-type label
        hw = attr.get("highway", "")
        if isinstance(hw, list):
            hw = hw[0]
        hw_labels = {
            "motorway": "Motorway", "trunk": "Trunk road",
            "primary": "Main road", "secondary": "Secondary road",
            "tertiary": "Side road", "residential": "Residential street",
            "unclassified": "Local road", "service": "Service road",
        }
        return hw_labels.get(hw) if hw else None

    def _segment_display_name(self, u: int, v: int, index: int) -> str:
        name = self._street_name(u, v)
        return name if name else f"Stretch {index + 1}"

    # ── Plain-English reason builder ──────────────────────────────────────────

    def _plain_reason(self, top_factors: list, risk: float) -> str:
        """Convert top SHAP factors into one readable sentence."""
        bad  = [f for f in top_factors if f["direction"] == "increases_risk"]
        good = [f for f in top_factors if f["direction"] == "decreases_risk"]

        if not bad:
            return "This stretch looks relatively calm right now."

        primary = bad[0]["label"].lower()

        if "this hour" in primary:
            base = "Higher than usual crime activity reported here right now."
        elif "night" in primary:
            base = "This area sees more crime at night — stay aware."
        elif "evening" in primary:
            base = "Evening crime activity is elevated on this stretch."
        elif "morning" in primary:
            base = "Morning crime incidents are higher here than average."
        elif "afternoon" in primary:
            base = "Afternoon crime is above average in this area."
        elif "weekend" in primary:
            base = "This area has a concentration of weekend crime incidents."
        elif "density" in primary or "area" in primary:
            base = "This stretch passes through a higher crime density area."
        elif "nearby" in primary or "local" in primary:
            base = "There are more crime incidents close to this road."
        elif "proximity" in primary or "distance" in primary:
            base = "This road is closer to known crime hotspots."
        elif "central" in primary or "hub" in primary:
            base = "This is a busy traffic hub — more exposure."
        elif "intersection" in primary:
            base = "Multiple intersections here mean more stopping points."
        else:
            base = f"{bad[0]['label']} is elevated on this stretch."

        if good:
            good_label = good[0]["label"].lower()
            if "nearby" in good_label or "local" in good_label:
                base += " The immediate surroundings are quieter."
            elif "density" in good_label or "area" in good_label:
                base += " The broader area crime density is lower."

        return base

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_predict_df(self, hour: int) -> pd.DataFrame:
        df = self.edges_ml.copy()
        df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        df["hour_cos"] = np.cos(2 * np.pi * hour / 24)

        df["crimes_night"]     = self._hour_matrix[:, [0,1,2,3,4,5]].sum(axis=1)
        df["crimes_morning"]   = self._hour_matrix[:, [6,7,8,9,10,11]].sum(axis=1)
        df["crimes_afternoon"] = self._hour_matrix[:, [12,13,14,15,16,17]].sum(axis=1)
        df["crimes_evening"]   = self._hour_matrix[:, [18,19,20,21,22,23]].sum(axis=1)

        for col in ["crimes_night","crimes_morning","crimes_afternoon","crimes_evening"]:
            max_val = df[col].max()
            if max_val > 0:
                df[col] /= max_val

        crimes_this_hour = self._hour_matrix[:, hour].astype(float)
        max_cth = crimes_this_hour.max()
        df["crimes_this_hour"] = (
            crimes_this_hour / max_cth if max_cth > 0 else crimes_this_hour
        )
        return df

    def _edge_lookup(self, predict_df: pd.DataFrame) -> dict:
        return {
            (int(r["u"]), int(r["v"])): idx
            for idx, r in predict_df.iterrows()
        }

    def _to_array(self, X) -> np.ndarray:
        return np.asarray(X.todense() if hasattr(X, "todense") else X)

    def _label(self, feature_name: str) -> str:
        return FEATURE_LABELS.get(feature_name, feature_name.replace("_", " ").title())

    # ── Public API ────────────────────────────────────────────────────────────

    def explain_edge(self, u: int, v: int, hour: int) -> dict:
        """Explain why a single road segment has its risk score at a given hour."""
        predict_df = self._build_predict_df(hour)
        lookup     = self._edge_lookup(predict_df)
        key        = (int(u), int(v))

        if key not in lookup:
            return {"error": f"Edge ({u}, {v}) not found in edge features"}

        row     = predict_df.loc[[lookup[key]], self.feature_cols]
        X_trans = self.preprocessor.transform(row)
        sv      = self.shap_explainer.shap_values(X_trans)[0]
        vals    = self._to_array(X_trans)[0]
        risk    = float(self.pipeline.predict(row)[0])

        contributions = sorted([
            {
                "feature"   : fname,
                "label"     : self._label(fname),
                "value"     : round(float(fval), 4),
                "shap_value": round(float(sv_), 4),
                "direction" : "increases_risk" if sv_ > 0 else "decreases_risk",
            }
            for fname, fval, sv_ in zip(self.feature_names, vals, sv)
        ], key=lambda x: abs(x["shap_value"]), reverse=True)

        top_label = contributions[0]["label"] if contributions else "unknown"
        direction = contributions[0]["direction"] if contributions else ""
        summary = (
            f"Risk score: {round(risk, 3)}. "
            f"Main driver: {top_label} ({'increases' if direction == 'increases_risk' else 'decreases'} risk)."
        )

        return {
            "edge"            : {"u": u, "v": v},
            "hour"            : hour,
            "predicted_risk"  : round(risk, 4),
            "base_value"      : round(float(self.shap_explainer.expected_value), 4),
            "summary"         : summary,
            "top_contributors": contributions[:5],
        }

    def explain_route(self, path: list, hour: int, top_n: int = 3) -> dict:
        """Explain risk across an entire route with human-friendly output."""
        predict_df = self._build_predict_df(hour)
        lookup     = self._edge_lookup(predict_df)

        edge_risks = []
        all_shap   = []

        for u, v in zip(path[:-1], path[1:]):
            key = (int(u), int(v))
            if key not in lookup:
                continue

            row     = predict_df.loc[[lookup[key]], self.feature_cols]
            X_trans = self.preprocessor.transform(row)
            sv      = self.shap_explainer.shap_values(X_trans)[0]
            risk    = float(self.pipeline.predict(row)[0])

            all_shap.append(sv)

            top3 = sorted(
                zip(self.feature_names, sv),
                key=lambda x: abs(x[1]), reverse=True
            )[:3]

            top_factors = [
                {
                    "feature"   : f,
                    "label"     : self._label(f),
                    "shap_value": round(float(s), 4),
                    "direction" : "increases_risk" if s > 0 else "decreases_risk",
                }
                for f, s in top3
            ]

            edge_risks.append({
                "u"          : u,
                "v"          : v,
                "risk"       : round(risk, 4),
                "top_factors": top_factors,
            })

        if not edge_risks:
            return {"error": "No matching edges found in route"}

        risks    = [e["risk"] for e in edge_risks]
        avg_risk = float(np.mean(risks))

        if avg_risk < 0.2:   risk_level = "Low"
        elif avg_risk < 0.4: risk_level = "Moderate"
        elif avg_risk < 0.6: risk_level = "High"
        else:                risk_level = "Very High"

        risk_arr = np.array(risks)
        total    = len(risk_arr)
        dist = {
            "low"      : round(float((risk_arr < 0.2).sum() / total), 3),
            "moderate" : round(float(((risk_arr >= 0.2) & (risk_arr < 0.4)).sum() / total), 3),
            "high"     : round(float(((risk_arr >= 0.4) & (risk_arr < 0.6)).sum() / total), 3),
            "very_high": round(float((risk_arr >= 0.6).sum() / total), 3),
        }

        mean_shap        = np.abs(np.vstack(all_shap)).mean(axis=0)
        dominant_idx     = int(mean_shap.argmax())
        dominant_feature = self.feature_names[dominant_idx]
        dominant_label   = self._label(dominant_feature)

        low_pct = round(dist["low"] * 100)
        mod_pct = round(dist["moderate"] * 100)
        hi_pct  = round(dist["high"] * 100)
        vh_pct  = round(dist["very_high"] * 100)

        summary = (
            f"Route has {risk_level.lower()} risk overall (avg {round(avg_risk, 2)}/1.0). "
            f"{low_pct}% of roads are low risk. "
            f"The main risk driver is: {dominant_label.lower()}."
        )

        # ── Sort by risk, then enrich with display names and plain reasons ──
        riskiest = sorted(edge_risks, key=lambda x: x["risk"], reverse=True)
        segments_to_watch = []
        for i, seg in enumerate(riskiest[:top_n]):
            r = seg["risk"]
            if r < 0.2:   caution = "Calm"
            elif r < 0.4: caution = "Moderate caution"
            elif r < 0.6: caution = "Stay alert"
            else:         caution = "High caution"

            segments_to_watch.append({
                "u"            : seg["u"],
                "v"            : seg["v"],
                "display_name" : self._segment_display_name(seg["u"], seg["v"], i),
                "caution_level": caution,
                "plain_reason" : self._plain_reason(seg["top_factors"], r),
                "risk"         : r,
                "top_factors"  : seg["top_factors"],
            })

        return {
            "avg_risk"             : round(avg_risk, 4),
            "risk_level"           : risk_level,
            "dominant_risk_factor" : dominant_label,
            "summary"              : summary,
            "risk_distribution"    : dist,
            "riskiest_segments"    : riskiest[:top_n],   # raw (debug)
            "segments_to_watch"    : segments_to_watch,  # human-friendly (UI)
        }

    def compare_routes(
        self,
        path_safest: list,
        path_fastest: list,
        hour: int,
        time_safest_min: float = None,
        time_fastest_min: float = None,
        dist_safest_km: float = None,
        dist_fastest_km: float = None,
    ) -> dict:
        """Explain WHY the safest route is safer than the fastest route."""
        predict_df = self._build_predict_df(hour)
        lookup     = self._edge_lookup(predict_df)

        def route_features(path):
            rows = []
            for u, v in zip(path[:-1], path[1:]):
                key = (int(u), int(v))
                if key in lookup:
                    rows.append(predict_df.loc[lookup[key], self.feature_cols])
            return pd.DataFrame(rows) if rows else pd.DataFrame()

        def route_avg_risk(path):
            risks = []
            for u, v in zip(path[:-1], path[1:]):
                key = (int(u), int(v))
                if key in lookup:
                    row = predict_df.loc[[lookup[key]], self.feature_cols]
                    risks.append(float(self.pipeline.predict(row)[0]))
            return float(np.mean(risks)) if risks else 0.0

        df_safe  = route_features(path_safest)
        df_fast  = route_features(path_fastest)

        if df_safe.empty or df_fast.empty:
            return {"error": "Could not extract features for one or both routes"}

        safest_risk   = route_avg_risk(path_safest)
        fastest_risk  = route_avg_risk(path_fastest)
        reduction     = fastest_risk - safest_risk
        reduction_pct = round((reduction / fastest_risk * 100) if fastest_risk > 0 else 0, 1)

        # ── Safety scores (0-100, higher = safer) ────────────────────────────
        safest_pct  = round((1 - safest_risk)  * 100)
        fastest_pct = round((1 - fastest_risk) * 100)

        # ── Time tradeoff sentence ────────────────────────────────────────────
        tradeoff_message = None
        if time_safest_min is not None and time_fastest_min is not None:
            extra_min = round(time_safest_min - time_fastest_min, 1)
            if extra_min > 0:
                tradeoff_message = (
                    f"{extra_min} extra min for a {reduction_pct}% calmer route"
                )
            elif extra_min < 0:
                tradeoff_message = (
                    f"Actually {abs(extra_min)} min faster and {reduction_pct}% safer"
                )
            else:
                tradeoff_message = f"Same travel time, {reduction_pct}% safer"

        num_cols = [f for f in self.feature_cols if f != "highway"]
        diffs = []
        for col in num_cols:
            safe_mean = float(df_safe[col].mean())  if col in df_safe.columns  else 0.0
            fast_mean = float(df_fast[col].mean())  if col in df_fast.columns  else 0.0
            diff      = safe_mean - fast_mean

            interp_pair    = DIFF_INTERPRETATIONS.get(col, (col, col))
            interpretation = interp_pair[0] if diff < 0 else interp_pair[1]

            diffs.append({
                "feature"       : col,
                "label"         : self._label(col),
                "safest_mean"   : round(safe_mean, 4),
                "fastest_mean"  : round(fast_mean, 4),
                "difference"    : round(diff, 4),
                "interpretation": interpretation,
            })

        diffs.sort(key=lambda x: abs(x["difference"]), reverse=True)

        # ── User-facing bullet list (top 4 meaningful differences only) ──────
        user_differences = [
            d["interpretation"]
            for d in diffs[:4]
            if abs(d["difference"]) > 0.001
        ]

        top_diff = diffs[0] if diffs else {}
        summary = (
            f"The safest route reduces risk by {reduction_pct}% "
            f"(from {round(fastest_risk, 2)} to {round(safest_risk, 2)}). "
            f"The biggest reason: {top_diff.get('interpretation', '').lower()}."
        )

        return {
            # ── User-facing ──────────────────────────────────────────────────
            "summary"            : summary,
            "tradeoff_message"   : tradeoff_message,
            "safest_pct"         : safest_pct,
            "fastest_pct"        : fastest_pct,
            "risk_reduction_pct" : reduction_pct,
            "user_differences"   : user_differences,
            "safest_travel_min"  : time_safest_min,
            "fastest_travel_min" : time_fastest_min,
            "safest_distance_km" : dist_safest_km,
            "fastest_distance_km": dist_fastest_km,
            # ── Debug / internal ─────────────────────────────────────────────
            "safest_avg_risk"    : round(safest_risk, 4),
            "fastest_avg_risk"   : round(fastest_risk, 4),
            "risk_reduction"     : round(reduction, 4),
            "key_differences"    : diffs[:6],
        }