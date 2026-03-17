import React, { useState, useEffect } from "react";
import axios from "axios";
import "leaflet/dist/leaflet.css";
import SplashScreen from "./components/splashScreen";
import Sidebar from "./components/sideBar";
import Map from "./components/map";
import { FaSpinner } from "react-icons/fa";

function haversineDistance(coord1, coord2) {
  const toRad = (x) => (x * Math.PI) / 180;
  const [lat1, lon1] = coord1;
  const [lat2, lon2] = coord2;
  const R  = 6371e3;
  const φ1 = toRad(lat1);
  const φ2 = toRad(lat2);
  const Δφ = toRad(lat2 - lat1);
  const Δλ = toRad(lon2 - lon1);
  const a  =
    Math.sin(Δφ / 2) ** 2 +
    Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

function getCurrentFormattedTime() {
  const now     = new Date();
  const hours   = String(now.getHours()).padStart(2, "0");
  const minutes = String(now.getMinutes()).padStart(2, "0");
  const seconds = String(now.getSeconds()).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

const BASE_URL = "http://127.0.0.1:5000";

const SafeRouteMap = () => {
  const [isLoading,       setIsLoading]       = useState(true);
  const [sourceCoords,    setSourceCoords]    = useState(null);
  const [destCoords,      setDestCoords]      = useState(null);
  const [routeType,       setRouteType]       = useState("");
  const [route,           setRoute]           = useState([]);
  const [routeInfo,       setRouteInfo]       = useState({});
  const [isSideBar,       setIsSideBar]       = useState(true);
  const [loading,         setLoading]         = useState(false);
  // XAI state — populated in the background after the route loads
  const [explanationData, setExplanationData] = useState(null);
  const [comparisonData,  setComparisonData]  = useState(null);

  useEffect(() => {
    const timer = setTimeout(() => setIsLoading(false), 2500);
    return () => clearTimeout(timer);
  }, []);

  // Clear route and XAI data when either endpoint changes
  useEffect(() => {
    setRoute([]);
    setExplanationData(null);
    setComparisonData(null);
  }, [sourceCoords, destCoords]);

  useEffect(() => {
    if (Object.keys(routeInfo).length > 0) {
      console.log("Route Info:", routeInfo);
    }
  }, [routeInfo]);

  // ── Shared params builder ────────────────────────────────────────────────
  const buildParams = (extraParams = {}) => ({
    source:      `${sourceCoords[0]},${sourceCoords[1]}`,
    destination: `${destCoords[0]},${destCoords[1]}`,
    time:        getCurrentFormattedTime(),
    ...extraParams,
  });

  // ── Background XAI fetch — fires after route is drawn, non-blocking ──────
  const fetchExplanation = async (routeTypeParam) => {
    try {
      const res = await axios.get(`${BASE_URL}/explain_route`, {
        params: buildParams({ route_type: routeTypeParam }),
      });
      setExplanationData(res.data.explanation);
    } catch (err) {
      console.warn("XAI explain_route failed (non-critical):", err.message);
    }
  };

  // ── Background comparison fetch — only meaningful for safest / fastest ───
  const fetchComparison = async () => {
    try {
      const res = await axios.get(`${BASE_URL}/compare_routes`, {
        params: buildParams(),
      });
      setComparisonData(res.data.comparison);
    } catch (err) {
      console.warn("XAI compare_routes failed (non-critical):", err.message);
    }
  };

  // ── Main route fetch ─────────────────────────────────────────────────────
  const fetchSafeRoute = async () => {
    if (!sourceCoords || !destCoords || !routeType) return;

    try {
      setLoading(true);
      // Clear stale XAI data from previous route
      setExplanationData(null);
      setComparisonData(null);

      const response = await axios.get(`${BASE_URL}/find_safe_route`, {
        params: buildParams({ route_type: routeType }),
      });

      const backendRoute = response.data.route;
      const routeData    = response.data.info;

      if (!backendRoute || backendRoute.length < 2) {
        alert("No valid route received from backend.");
        return;
      }

      const startDist = haversineDistance(sourceCoords, backendRoute[0]);
      const endDist   = haversineDistance(destCoords,   backendRoute[backendRoute.length - 1]);
      const threshold = 2400;

      if (startDist > threshold || endDist > threshold) {
        alert(
          "Sorry for the inconvenience, but we do not have a route available for the provided locations at the moment."
        );
        return;
      }

      setRoute(backendRoute);
      setRouteInfo(routeData);

      // Fire XAI requests in the background — do not await, won't block UI
      fetchExplanation(routeType);
      // Only compare when user picked a single-objective route
      if (routeType === "safest" || routeType === "fastest") {
        fetchComparison();
      }

    } catch (error) {
      console.error("Error fetching route:", error);
      alert("An error occurred while fetching the route.");
    } finally {
      setLoading(false);
    }
  };

  if (isLoading) return <SplashScreen />;

  return (
    <div className="flex h-screen w-screen map-page">
      {loading && (
        <div className="absolute top-0 left-0 w-full h-full bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 transition-opacity duration-300 ease-in-out">
          <div className="flex flex-col items-center text-white">
            <FaSpinner className="animate-spin text-4xl mb-3" />
            <p className="text-lg font-semibold">Finding the safest route...</p>
          </div>
        </div>
      )}

      <Sidebar
        sourceCoords={sourceCoords}
        destCoords={destCoords}
        setSourceCoords={setSourceCoords}
        setDestCoords={setDestCoords}
        fetchSafeRoute={fetchSafeRoute}
        isSideBar={isSideBar}
        setIsSideBar={setIsSideBar}
        routeType={routeType}
        setRouteType={setRouteType}
        routeInfo={routeInfo}
        loading={loading}
        explanationData={explanationData}
        comparisonData={comparisonData}
      />

      <Map
        sourceCoords={sourceCoords}
        destCoords={destCoords}
        route={route}
        isSideBar={isSideBar}
        setIsSideBar={setIsSideBar}
      />
    </div>
  );
};

export default SafeRouteMap;