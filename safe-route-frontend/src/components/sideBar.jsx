import {useRef, useState} from 'react'
import axios from "axios";
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

export default function sidebar({sourceCoords, destCoords,setSourceCoords, setDestCoords, fetchSafeRoute, isSideBar, setIsSideBar, routeType, setRouteType, routeInfo,loading}){
    const [source, setSource] = useState("");
    const [destination, setDestination] = useState("");
    const sidebarDiv = useRef(null)
    
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
            // Expanding: width first, then opacity
            tl.to(sidebarDiv.current, {
            width: 360,
            pointerEvents: "auto",
            });

            tl.to(sidebarDiv.current, {
            opacity: 1,
            ease: "power2.inOut",
            });

        } else {
            // Collapsing: opacity first, then width
            tl.to(sidebarDiv.current, {
            opacity: 0,
            duration: 0.5,
            ease: "power2.inOut",
            });
            
            tl.to(sidebarDiv.current, {
            width: 0,
            pointerEvents: "none",
            });
        }
    }, [isSideBar]);

    return(
        <div ref={sidebarDiv} className="sidebar-container">
            <div className="sidebar-header">
                <div className="logo">
                    <i className="fas fa-route"></i>
                    <h1>SafeRoute Navigator</h1>
                </div>
                <button className="toggle-sidebar" onClick={() => setIsSideBar(false)}>
                    <i className="fas fa-chevron-left"></i>
                </button>
            </div>
            
            <form
                onSubmit={(e) => {
                    e.preventDefault();
                    fetchSafeRoute();
                }}
                className="search-container"
            >
                <div className="input-group">
                    <label htmlFor="source"><i className="fas fa-circle"></i> Source</label>
                    <input
                        id="source"
                        type="text"
                        placeholder="Enter starting point"
                        value={source}
                        onChange={(e) => setSource(e.target.value)}
                        onBlur={() => fetchCoordinates(source, "source")}
                    />
                </div>
                
                <div className="input-group">
                    <label htmlFor="destination"><i className="fas fa-flag"></i> Destination</label>
                    <input
                        id="destination"
                        type="text"
                        placeholder="Enter destination"
                        value={destination}
                        onChange={(e) => setDestination(e.target.value)}
                        onBlur={() => fetchCoordinates(destination, "destination")}
                    />
                </div>
                
                <div className="input-group">
                    <label htmlFor="route-type"><i className="fas fa-route"></i> Route Type</label>
                    <select
                        id="route-type"
                        value={routeType}
                        onChange={(e) => setRouteType(e.target.value)}
                    >
                        <option value="" disabled hidden>Select a Route Option</option>
                        <option value="safest">Safest Route</option>
                        <option value="fastest">Fastest Route</option>
                        <option value="safest_fastest">Safest and Fastest Route</option>
                    </select>
                </div>

                <button 
                    className="find-route-btn" 
                    type="submit" 
                    disabled={!sourceCoords || !destCoords || !routeType}
                >
                    {loading ? (
                        <>
                            <i className="fas fa-spinner fa-spin"></i> Finding Route...
                        </>
                    ) : (
                        <>
                            <i className="fas fa-search"></i> Find Safe Route
                        </>
                    )}
                </button>
            </form>
            
            {routeInfo && Object.keys(routeInfo).length > 0 && (
                <div className="route-info">
                    <h3><i className="fas fa-info-circle"></i> Route Information</h3>
                    <div className="info-item">
                        <span className="info-label"><i className="fas fa-route"></i> Distance</span>
                        <span className="info-value">{routeInfo.distance}</span>
                    </div>
                    <div className="info-item">
                        <span className="info-label"><i className="fas fa-clock"></i> Duration</span>
                        <span className="info-value">{routeInfo.duration}</span>
                    </div>
                    <div className="info-item">
                        <span className="info-label"><i className="fas fa-shield-alt"></i> Safety Level</span>
                        <span className="info-value">{routeInfo.safety_level}</span>
                    </div>
                        <div className="info-item">
                    <span className="info-label">
                        <i className="fas fa-star"></i> Safety Score
                    </span>
                    <span className="info-value">
                        <span
                        className={`safety-score ${
                            routeInfo.safety_score >= 7
                            ? "score-low"
                            : routeInfo.safety_score >= 4
                            ? "score-medium"
                            : "score-high"
                        }`}
                        >
                        {routeInfo.safety_score}
                        </span>
                    </span>
                    </div>

                </div>
            )}
        </div>
    )
}