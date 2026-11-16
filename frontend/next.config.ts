import type { NextConfig } from "next";

// Where the Next.js server forwards /api/* (server side). Resolved at build time:
// http://localhost:8000 for `npm run dev`, http://backend:8000 inside compose.
const apiInternalUrl = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker image (frontend/Dockerfile).
  output: "standalone",
  // The browser only ever talks to this origin; the API needs no public port or CORS.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/:path*` }];
  },
};

export default nextConfig;
