import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Reason: static SPA export (master-plan Open Q4 + port-map §6). The app ships
  // as a static bundle wrapped in Capacitor for iOS — no server components,
  // no API routes, no on-the-fly image optimization.
  output: "export",
  // Reason: next/image optimization requires a server; static export forbids it.
  images: { unoptimized: true },
};

export default nextConfig;
