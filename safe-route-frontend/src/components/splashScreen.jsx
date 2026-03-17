// src/components/SplashScreen.jsx
import React, { useEffect, useRef } from "react";
import { gsap } from "gsap";
import { useGSAP } from '@gsap/react'

const SplashScreen = () => {
  const splashRef = useRef(null);

  useGSAP(() => {
    const timeline = gsap.timeline({
      delay: 2 // splash stays for 2 seconds
      
    });

    // Exit animation
    timeline.to(splashRef.current, {
      opacity: 0,
      scale: 0.9,
      duration: 0.8,
      ease: "power2.inOut",
    });
  }, []);

  return (
    <div
      ref={splashRef}
      className="flex items-center justify-center h-screen w-screen bg-blue-900 text-white text-center flex-col gap-4"
    >
      <h1 className="text-4xl font-bold animate-pulse">SafeRoute Navigator</h1>
      <p className="text-sm text-gray-200">Loading, please wait...</p>
      <div className="w-8 h-8 border-4 border-white border-t-transparent rounded-full animate-spin" />
    </div>
  );
};

export default SplashScreen;
