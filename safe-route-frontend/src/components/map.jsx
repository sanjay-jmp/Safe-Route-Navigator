import L from "leaflet";
import { MapContainer, TileLayer, Marker, Polyline, Popup } from "react-leaflet";
import { FaBars } from 'react-icons/fa';

const sourceIcon = new L.Icon({
    iconUrl: "https://cdn-icons-png.flaticon.com/512/535/535239.png",
    iconSize: [30, 30],
    iconAnchor: [15, 30],
});
    
const destIcon = new L.Icon({
    iconUrl: "https://cdn-icons-png.flaticon.com/512/684/684908.png",
    iconSize: [30, 30],
    iconAnchor: [15, 30],
});

const center = [34.0522, -118.2437];

export default function map({sourceCoords, destCoords, route, isSideBar, setIsSideBar}){
    
    return(
        <div className="flex-1 h-full">
            {!isSideBar && (
            <button 
                className="absolute top-4 left-14 bg-blue-600 text-white px-3 py-2 rounded shadow z-[999]"
                
                onClick={()=>setIsSideBar(true)}>
                <FaBars/>
            </button>)}
            <MapContainer center={center} zoom={12} className="h-full w-full z-0">
                <TileLayer
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    attribution='&copy; OpenStreetMap contributors'
                />

                {sourceCoords && (
                    <Marker position={sourceCoords} icon={sourceIcon}>
                      <Popup>Source</Popup>
                    </Marker>
                )}

                {destCoords && (
                    <Marker position={destCoords} icon={destIcon}>
                      <Popup>Destination</Popup>
                    </Marker>
                )}

                {route.length > 0 && <Polyline positions={route} color="blue" />}
            </MapContainer>
        </div>
    )
}