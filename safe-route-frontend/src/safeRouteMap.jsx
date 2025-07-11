import React, { useState, useEffect} from "react";
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

  const R = 6371e3; // Earth radius in meters
  const φ1 = toRad(lat1);
  const φ2 = toRad(lat2);
  const Δφ = toRad(lat2 - lat1);
  const Δλ = toRad(lon2 - lon1);

  const a =
    Math.sin(Δφ / 2) ** 2 +
    Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

  return R * c; // distance in meters
}


function getCurrentFormattedTime() {
  const now = new Date();

  // Get hours, minutes, and seconds
  let hours = now.getHours();
  let minutes = now.getMinutes();
  let seconds = now.getSeconds();

  // Pad with leading zero if less than 10
  hours = hours < 10 ? '0' + hours : hours;
  minutes = minutes < 10 ? '0' + minutes : minutes;
  seconds = seconds < 10 ? '0' + seconds : seconds;

  // Combine into the desired format
  return `${hours}:${minutes}:${seconds}`;
}

const currentTime = getCurrentFormattedTime();
console.log(currentTime); // Example output: "12:45:00"
const SafeRouteMap = () => {
  const [isLoading, setIsLoading] = useState(true);
  const [sourceCoords, setSourceCoords] = useState(null);
  const [destCoords, setDestCoords] = useState(null);
  const [routeType, setRouteType] = useState("");
  const [route, setRoute] = useState([]);
  const [routeInfo, setRouteInfo] = useState({})
  const [isSideBar, setIsSideBar] = useState(true);
  const [loading, setLoading] = useState(false);


  useEffect(() => {
    const timer = setTimeout(() => setIsLoading(false), 2500);
    return () => clearTimeout(timer);
  }, []);
  useEffect(() => {
  setRoute([]);  // Clear the route when source or destination changes
}, [sourceCoords, destCoords]);

 useEffect(() => {
  if (Object.keys(routeInfo).length > 0) {
    console.log("🛣️ Route Info:", routeInfo);
  }
}, [routeInfo]);

  
  const fetchSafeRoute = async () => {
  if (!sourceCoords || !destCoords || !routeType) return;

  try {
    setLoading(true);
    const response = await axios.get("https://safe-route-navigator.onrender.com/find-safe-route", {
      params: {
        source: `${sourceCoords[0]},${sourceCoords[1]}`,
        destination: `${destCoords[0]},${destCoords[1]}`,
        time: getCurrentFormattedTime(),
        route_type: routeType,
      },
    });

    const backendRoute = response.data.route;
    const routeInfo = response.data.info;

    // Validate coordinates
    if (!backendRoute || backendRoute.length < 2) {
      alert("No valid route received from backend.");
      return;
    }

    const startBackend = backendRoute[0];
    const endBackend = backendRoute[backendRoute.length - 1];

    const startDist = haversineDistance(sourceCoords, startBackend);
    const endDist = haversineDistance(destCoords, endBackend);

    const threshold = 200; // meters

    if (startDist > threshold || endDist > threshold) {
      alert(
        "Sorry for the inconvenience, but we do not have a route available for the provided locations at the moment."
      );
      return;
    }

    // All good → set the route and info
    setRoute(backendRoute);
    setRouteInfo(routeInfo);
  } catch (error) {
    console.error("Error fetching route:", error);
    alert("An error occurred while fetching the route.");
  }
  finally {
    setLoading(false); // ✅ Stop loading
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
