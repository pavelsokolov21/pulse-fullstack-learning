import type { NextConfig } from "next";

// Адрес FastAPI, на который уходит /api/*. Читается на сервере Next.js при
// сборке и старте, в браузер не попадает (поэтому без NEXT_PUBLIC_).
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  async redirects() {
    return [{ source: "/", destination: "/monitors", permanent: false }];
  },
  async rewrites() {
    // Прокси: браузер видит один origin, CORS не нужен. Cookie с refresh-токеном
    // должна ходить на /api/auth (PULSE_API_REFRESH_COOKIE_PATH на бэкенде).
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
