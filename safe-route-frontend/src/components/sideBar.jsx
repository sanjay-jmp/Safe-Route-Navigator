import { useRef, useState } from "react";
import axios from "axios";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";

export default function Sidebar({
  sourceCoords, destCoords, setSourceCoords, setDestCoords,
  fetchSafeRoute, isSideBar, setIsSideBar,
  routeType, setRouteType, routeInfo, loading,
  explanationData, comparisonData,
}) {
  const [source,      setSource]      = useState("");
  const [destination, setDestination] = useState("");
  const sidebarDiv = useRef(null);

  const fetchCoordinates = async (address, type) => {
    if (!address) return;
    try {
      const response = await axios.get("https://nominatim.openstreetmap.org/search", {
        params: { q: address, format: "json" },
      });
      if (response.data.length > 0) {
        const { lat, lon } = response.data[0];
        const coords = [parseFloat(lat), parseFloat(lon)];
        type === "source" ? setSourceCoords(coords) : setDestCoords(coords);
      }
    } catch (error) {
      console.error("Error fetching coordinates:", error);
    }
  };

  useGSAP(() => {
    if (!sidebarDiv.current) return;
    const tl = gsap.timeline();
    if (isSideBar) {
      tl.to(sidebarDiv.current, { width: 360, pointerEvents: "auto" });
      tl.to(sidebarDiv.current, { opacity: 1, ease: "power2.inOut" });
    } else {
      tl.to(sidebarDiv.current, { opacity: 0, duration: 0.5, ease: "power2.inOut" });
      tl.to(sidebarDiv.current, { width: 0, pointerEvents: "none" });
    }
  }, [isSideBar]);

  // ── Risk scale: 0–100 safety score bands ──────────────────────────────────
  //   70–100  →  Safe      (green)
  //   30–70   →  Moderate  (amber)
  //    0–30   →  High Risk (red)

  // Converts a 0–1 raw risk into a 0–100 safety score (higher = safer)
  const safetyScore = (risk) => {
    if (risk === undefined || risk === null) return 0;
    return Math.round((1 - risk) * 100);
  };

  // Colour by 0–1 raw risk, thresholds aligned to new bands
  const riskColour = (risk) => {
    const score = safetyScore(risk);   // convert to 0–100 first
    if (score >= 70) return "#22c55e"; // Safe
    if (score >= 30) return "#f59e0b"; // Moderate
    return "#ef4444";                  // High Risk
  };

  // ── FIX 2: factorEmoji was called throughout but never defined ─────────────
  const factorEmoji = (label) => {
    if (!label) return "⚠️";
    const l = label.toLowerCase();
    if (l.includes("crime")) return "🚨";
    if (l.includes("density") || l.includes("area")) return "📍";
    if (l.includes("intersection")) return "🔀";
    if (l.includes("traffic") || l.includes("central") || l.includes("hub")) return "🚗";
    if (l.includes("night")) return "🌙";
    if (l.includes("evening")) return "🌆";
    if (l.includes("morning")) return "🌅";
    if (l.includes("weekend")) return "📅";
    return "⚠️";
  };

  // riskLabel — uses the new 0–30 / 30–70 / 70–100 bands
  const riskLabel = (risk) => {
    const score = safetyScore(risk);
    if (score >= 70) return { text: "Calm",       colour: "#22c55e" };
    if (score >= 30) return { text: "Moderate",   colour: "#f59e0b" };
    return                   { text: "High Alert", colour: "#ef4444" };
  };

  // segmentTip — aligned to same bands
  const segmentTip = (i, risk) => {
    const score = safetyScore(risk);
    if (score >= 70) return "Generally calm — enjoy the route.";
    if (score >= 30) return "Stay aware of your surroundings here.";
    return "Move through this area quickly and stay alert.";
  };

  // ── Shared style tokens ────────────────────────────────────────────────────
  const muted = { fontSize: "11px", color: "#374151" };

  return (
    <div ref={sidebarDiv} className="sidebar-container">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="sidebar-header">
        <div className="logo">
          <i className="fas fa-route"></i>
          <h1>SafeRoute Navigator</h1>
        </div>
        <button className="toggle-sidebar" onClick={() => setIsSideBar(false)}>
          <i className="fas fa-chevron-left"></i>
        </button>
      </div>

      {/* ── Search form ─────────────────────────────────────────────────── */}
      <form onSubmit={(e) => { e.preventDefault(); fetchSafeRoute(); }} className="search-container">
        <div className="input-group">
          <label htmlFor="source"><i className="fas fa-circle"></i> Source</label>
          <input id="source" type="text" placeholder="Enter starting point"
            value={source} onChange={(e) => setSource(e.target.value)}
            onBlur={() => fetchCoordinates(source, "source")} />
        </div>
        <div className="input-group">
          <label htmlFor="destination"><i className="fas fa-flag"></i> Destination</label>
          <input id="destination" type="text" placeholder="Enter destination"
            value={destination} onChange={(e) => setDestination(e.target.value)}
            onBlur={() => fetchCoordinates(destination, "destination")} />
        </div>
        <div className="input-group">
          <label htmlFor="route-type"><i className="fas fa-route"></i> Route Type</label>
          <select id="route-type" value={routeType} onChange={(e) => setRouteType(e.target.value)}>
            <option value="" disabled hidden>Select a Route Option</option>
            <option value="safest">Safest Route</option>
            <option value="fastest">Fastest Route</option>
            <option value="safest_fastest">Safest and Fastest Route</option>
          </select>
        </div>
        <button className="find-route-btn" type="submit" disabled={!sourceCoords || !destCoords || !routeType}>
          {loading
            ? <><i className="fas fa-spinner fa-spin"></i> Finding Route...</>
            : <><i className="fas fa-search"></i> Find Safe Route</>}
        </button>
      </form>

      {/* ── Route info ──────────────────────────────────────────────────── */}
      {routeInfo && Object.keys(routeInfo).length > 0 && (
        <div className="route-info">
          <h3><i className="fas fa-info-circle"></i> Route Information</h3>
          {[
            { icon: "fa-route",      label: "Distance",     value: routeInfo.distance },
            { icon: "fa-clock",      label: "Duration",     value: routeInfo.duration },
            { icon: "fa-shield-alt", label: "Safety Level", value: routeInfo.safety_level },
          ].map(({ icon, label, value }) => (
            <div key={label} className="info-item">
              <span className="info-label"><i className={`fas ${icon}`}></i> {label}</span>
              <span className="info-value">{value}</span>
            </div>
          ))}
          <div className="info-item">
            <span className="info-label"><i className="fas fa-star"></i> Safety Score</span>
            <span className="info-value">
              <span className={`safety-score ${
                routeInfo.safety_score >= 7 ? "score-low"
                : routeInfo.safety_score >= 3 ? "score-medium" : "score-high"
              }`}>{routeInfo.safety_score}</span>
            </span>
          </div>
        </div>
      )}

      {/* ── XAI loading indicator ────────────────────────────────────────── */}
      {routeInfo && Object.keys(routeInfo).length > 0 && !explanationData && (
        <div style={{ padding: "10px 16px", ...muted, display: "flex", alignItems: "center", gap: "6px" }}>
          <i className="fas fa-spinner fa-spin" style={{ fontSize: "10px" }}></i>
          Analysing route risk...
        </div>
      )}

      {/* ── XAI: Why This Route? ─────────────────────────────────────────── */}
      {explanationData && (
        <div className="route-info" style={{ marginTop: "8px" }}>

          {/* Hero banner */}
          <div style={{
            background: "linear-gradient(135deg, rgba(240,253,244,0.95) 0%, rgba(220,252,231,0.95) 100%)",
            borderRadius: "10px", padding: "14px", marginBottom: "12px",
            borderLeft: "4px solid #22c55e",
          }}>
            <div style={{ fontSize: "20px", marginBottom: "4px" }}>🛡️</div>
            <div style={{ fontSize: "13px", fontWeight: "700", color: "#111827" }}>
              You&apos;re on the safest route
            </div>
            <div style={{ fontSize: "11px", color: "#374151", marginTop: "4px", lineHeight: "1.4" }}>
              {explanationData.avg_risk !== undefined
                ? `Safety score ${safetyScore(explanationData.avg_risk)}/100 — most of this route is manageable.`
                : explanationData.summary}
            </div>
          </div>

          {/* Safety score meter */}
          {explanationData.avg_risk !== undefined && (() => {
            const score = safetyScore(explanationData.avg_risk);
            const sc = score >= 70 ? "#22c55e" : score >= 30 ? "#f59e0b" : "#ef4444";
            return (
              <div style={{ marginBottom: "12px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                  <span style={muted}>Overall safety score</span>
                  <span style={{ fontSize: "11px", fontWeight: "700", color: sc }}>{score} / 100</span>
                </div>
                <div style={{ height: "8px", background: "rgba(255,255,255,0.08)", borderRadius: "99px", overflow: "hidden" }}>
                  <div style={{ width: `${score}%`, height: "100%", borderRadius: "99px",
                    background: `linear-gradient(90deg, ${sc}, ${sc}88)` }} />
                </div>
              </div>
            );
          })()}

          {/* What's ahead — risk distribution */}
          {explanationData.risk_distribution && (() => {
            const dist = explanationData.risk_distribution;
            const bands = [
              { key: "low",       colour: "#22c55e", label: "Safe"       },
              { key: "moderate",  colour: "#f59e0b", label: "Moderate"   },
              { key: "high",      colour: "#f97316", label: "Stay alert" },
              { key: "very_high", colour: "#ef4444", label: "High alert" },
            ];
            return (
              <div style={{ marginBottom: "12px" }}>
                <div style={{ fontSize: "12px", fontWeight: "600", color: "#111827", marginBottom: "6px" }}>
                  What&apos;s ahead on this route?
                </div>
                <div style={{ display: "flex", height: "10px", borderRadius: "99px", overflow: "hidden", marginBottom: "6px" }}>
                  {bands.map(({ key, colour }) => {
                    const pct = (dist[key] || 0) * 100;
                    return pct > 0 ? <div key={key} style={{ width: `${pct}%`, background: colour }}
                      title={`${key.replace("_"," ")}: ${Math.round(pct)}%`} /> : null;
                  })}
                </div>
                <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                  {bands.map(({ key, colour, label }) => {
                    const pct = Math.round((dist[key] || 0) * 100);
                    return pct > 0 ? <span key={key} style={{ fontSize: "10px", color: colour }}>● {label} {pct}%</span> : null;
                  })}
                </div>
              </div>
            );
          })()}

          {/* Main risk driver */}
          {explanationData.dominant_risk_factor && (
            <div style={{
              display: "flex", alignItems: "center", gap: "8px",
              background: "rgba(251,191,36,0.15)", borderRadius: "8px",
              padding: "8px 10px", marginBottom: "12px",
              border: "1px solid rgba(251,191,36,0.4)",
            }}>
              <span style={{ fontSize: "16px" }}>{factorEmoji(explanationData.dominant_risk_factor)}</span>
              <div>
                <div style={{ fontSize: "9px", color: "#374151", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                  Main thing to know
                </div>
                <div style={{ fontSize: "12px", color: "#92400e", fontWeight: "600", marginTop: "1px" }}>
                  {explanationData.dominant_risk_factor}
                </div>
              </div>
            </div>
          )}

          {/* Segments to watch */}
          {(explanationData.segments_to_watch ?? explanationData.riskiest_segments)?.length > 0 && (
            <div>
              <div style={{ fontSize: "12px", fontWeight: "600", color: "#111827", marginBottom: "6px" }}>
                Stretches that need awareness
              </div>
              {(explanationData.segments_to_watch ?? explanationData.riskiest_segments).slice(0, 3).map((seg, i) => {
                const rl          = riskLabel(seg.risk);
                const displayName = seg.display_name ?? `Stretch ${i + 1}`;
                const reason      = seg.plain_reason
                  ?? (() => {
                       const bad = seg.top_factors?.find(f => f.direction === "increases_risk");
                       return bad ? `${bad.label} is elevated here.` : "Stay aware on this stretch.";
                     })();
                const caution     = seg.caution_level ?? rl.text;
                return (
                  <div key={i} style={{
                    marginTop: "6px", padding: "10px 12px",
                    background: safetyScore(seg.risk) < 30
                      ? "rgba(239,68,68,0.18)"
                      : safetyScore(seg.risk) < 70
                        ? "rgba(245,158,11,0.15)"
                        : "rgba(34,197,94,0.12)",
                    borderRadius: "10px",
                    borderLeft: `3px solid ${rl.colour}`,
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "5px" }}>
                      <span style={{ fontSize: "12px", fontWeight: "600", color: "#111827" }}>
                        📍 {displayName}
                      </span>
                      <span style={{
                        fontSize: "10px", fontWeight: "700", color: rl.colour,
                        background: `${rl.colour}22`, padding: "2px 8px", borderRadius: "99px",
                        whiteSpace: "nowrap", marginLeft: "6px",
                      }}>
                        {caution}
                      </span>
                    </div>
                    <div style={{ fontSize: "11px", color: "#374151", lineHeight: "1.5", marginBottom: "4px" }}>
                      {factorEmoji(seg.top_factors?.[0]?.label ?? "")} {reason}
                    </div>
                    <div style={{
                      fontSize: "10px", color: "#4b5563",
                      background: "rgba(0,0,0,0.04)",
                      borderRadius: "5px", padding: "4px 6px",
                    }}>
                      💡 {segmentTip(i, seg.risk)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── XAI: Safest vs Fastest ───────────────────────────────────────── */}
      {comparisonData && (
        <div className="route-info" style={{ marginTop: "8px" }}>
          <h3><i className="fas fa-balance-scale"></i> Safest vs Fastest</h3>

          {/* Tradeoff callout */}
          <div style={{
            display: "flex", alignItems: "center", gap: "10px",
            background: "rgba(34,197,94,0.10)", borderRadius: "8px",
            padding: "10px 12px", marginBottom: "12px",
            border: "1px solid rgba(34,197,94,0.3)",
          }}>
            <span style={{ fontSize: "18px" }}>⚡</span>
            <div style={{ flex: 1, fontSize: "12px", color: "#1f2937", lineHeight: "1.4" }}>
              {comparisonData.tradeoff_message
                ? comparisonData.tradeoff_message
                : <>The fastest route carries <strong style={{ color: "#111827" }}>noticeably more risk</strong></>}
            </div>
            <div style={{
              fontSize: "13px", fontWeight: "700", color: "#22c55e",
              background: "rgba(34,197,94,0.15)", padding: "4px 10px",
              borderRadius: "99px", whiteSpace: "nowrap",
            }}>
              +{comparisonData.risk_reduction_pct}% safer
            </div>
          </div>

          {/* Side-by-side safety scores */}
          <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
            {[
              {
                label:  "🛡️ This route",
                score:  comparisonData.safest_pct  ?? safetyScore(comparisonData.safest_avg_risk),
                colour: "#22c55e",
                sub:    comparisonData.safest_travel_min ? `${comparisonData.safest_travel_min} min` : "safety score",
              },
              {
                label:  "⚡ Fastest",
                score:  comparisonData.fastest_pct ?? safetyScore(comparisonData.fastest_avg_risk),
                colour: "#f97316",
                sub:    comparisonData.fastest_travel_min ? `${comparisonData.fastest_travel_min} min` : "safety score",
              },
            ].map(({ label, score, colour, sub }) => (
              <div key={label} style={{
                flex: 1, padding: "10px 8px",
                background: "rgba(0,0,0,0.04)",
                borderRadius: "8px", textAlign: "center",
              }}>
                <div style={{ fontSize: "10px", color: "#374151", marginBottom: "4px" }}>{label}</div>
                <div style={{ fontSize: "22px", fontWeight: "700", color: colour }}>{score}</div>
                <div style={{ fontSize: "9px", color: "#4b5563" }}>{sub}</div>
                <div style={{ height: "4px", background: "rgba(0,0,0,0.08)", borderRadius: "99px", marginTop: "6px", overflow: "hidden" }}>
                  <div style={{ width: `${score}%`, height: "100%", background: colour, borderRadius: "99px" }} />
                </div>
              </div>
            ))}
          </div>

          {/* Why is it safer */}
          {((comparisonData.user_differences ?? comparisonData.key_differences?.map(d => d.interpretation)) ?? []).length > 0 && (
            <div>
              <div style={{ fontSize: "12px", fontWeight: "600", color: "#111827", marginBottom: "8px" }}>
                Why is this route safer?
              </div>
              {(comparisonData.user_differences ?? comparisonData.key_differences.map(d => d.interpretation)).slice(0, 4).map((text, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: "8px", marginBottom: "7px" }}>
                  <span style={{ fontSize: "14px", flexShrink: 0, marginTop: "1px" }}>
                    {factorEmoji(comparisonData.key_differences?.[i]?.label ?? "")}
                  </span>
                  <span style={{ fontSize: "11px", color: "#374151", lineHeight: "1.4" }}>{text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

    </div>
  );
}