import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  basePath: "/restart",
  // The SSH port-forwarded preview loads dev assets from loopback instead of
  // the container's 0.0.0.0 hostname. Permit that origin so client components
  // hydrate and the demo item request can run in the browser.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
