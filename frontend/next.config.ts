import type { NextConfig } from "next";

// Where Django serves /api/v1 (e.g. http://localhost:8000). When set, the
// browser reaches Django through this origin (same-origin requests, see
// backend/ARCHITECTURE.md). When unset, Django-backed pages show that the
// feature isn't available instead of failing.
const djangoOrigin = process.env.DJANGO_API_ORIGIN?.replace(/\/+$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    if (!djangoOrigin) return [];
    return [{ source: "/api/v1/:path*", destination: `${djangoOrigin}/api/v1/:path*` }];
  },
};

export default nextConfig;
