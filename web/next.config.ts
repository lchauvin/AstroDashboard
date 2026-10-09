import type { NextConfig } from "next";

const ENGINE_URL = process.env.ASTRODASH_ENGINE_URL ?? "http://127.0.0.1:3007";

const nextConfig: NextConfig = {
  // The browser talks to the engine through this same-origin prefix.
  async rewrites() {
    return [{ source: "/engine/:path*", destination: `${ENGINE_URL}/:path*` }];
  },
  async redirects() {
    return [{ source: "/", destination: "/library", permanent: false }];
  },
};

export default nextConfig;
