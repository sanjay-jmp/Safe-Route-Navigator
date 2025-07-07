import {useRef, useState} from 'react'
import axios from "axios";
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

export default function sidebar({sourceCoords, destCoords,setSourceCoords, setDestCoords, fetchSafeRoute, isSideBar, setIsSideBar, routeType, setRouteType, routeInfo}){
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
                width: 300,
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
        <div ref ={sidebarDiv} className="w-[300px] bg-gray-100 p-6 overflow-y-auto shadow-md sidebar justify-items-center">
            <p onClick={()=> setIsSideBar(false)} className='my-4 text-blue-900 text-2xl font-semibold cursor-pointer \'>SafeRoute Navigator</p>
            <form
            onSubmit={(e) => {
                e.preventDefault();
                fetchSafeRoute();
            }}
            className="search-container"
            >
            <input
                type="text"
                placeholder="Enter Source"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                onBlur={() => fetchCoordinates(source, "source")}
            />
            <input
                type="text"
                placeholder="Enter Destination"
                value={destination}
                onChange={(e) => setDestination(e.target.value)}
                onBlur={() => fetchCoordinates(destination, "destination")}
            />
            <select
            value={routeType}
            onChange={(e) => setRouteType(e.target.value)}
            className={`p-2 rounded border ${routeType === "" ? "text-gray-700" : "text-black"}`}
            >
            <option value="" disabled hidden className="text-gray-600">
                Select a Route Option
            </option>
            <option value="safest">Safest Route</option>
            <option value="fastest">Fastest Route</option>
            <option value="safest_fastest">Safest and Fastest Route</option>
            </select>

            <button className='rounded-3xl' type="submit" disabled={!sourceCoords || !destCoords}>
                Find Route
            </button>
            </form>
            {routeInfo && Object.keys(routeInfo).length > 0 && (
            <div className="mt-4 p-3 bg-white shadow rounded text-sm text-gray-700">
                <p><strong>Distance:</strong> {routeInfo.distance}</p>
                <p><strong>Duration:</strong> {routeInfo.duration}</p>
                <p><strong>Safety Level:</strong> {routeInfo.safety_level}</p>
                <p><strong>Safety Score:</strong> {routeInfo.safety_score}</p>
            </div>
            )}
        </div>
    )
}