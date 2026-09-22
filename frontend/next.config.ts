import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Release gates can build beside a running standalone server without
  // touching the live .next directory that Windows keeps locked.
  distDir: process.env.NEXT_DIST_DIR || ".next",

  // Enable standalone output for optimized Docker deployment
  output: "standalone",

  // Allow HMR connection via ngrok domain
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    "agrostological-kourtney-uncomplicated.ngrok-free.dev",
  ],

  // Experimental features
  // Type assertion needed: proxyClientMaxBodySize is valid in Next.js 15 but types lag behind
  experimental: {
    // Increase proxy body size limit for file uploads (default is 10MB)
    // This allows larger files to be uploaded through the /api/* rewrite proxy to FastAPI
    proxyClientMaxBodySize: '100mb',
    // Ask/legal answers often take 45-120s; Next rewrite default proxyTimeout is 30s and returns 500.
    proxyTimeout: 300000,
  } as NextConfig['experimental'],

  // API Rewrites: Proxy /api/* requests to FastAPI backend
  // This simplifies reverse proxy configuration - users only need to proxy to port 8502
  // Next.js handles internal routing to the API backend on port 5055
  async rewrites() {
    // INTERNAL_API_URL: Where Next.js server-side should proxy API requests
    // Default: http://127.0.0.1:5055 (single-container deployment)
    // Override for multi-container: INTERNAL_API_URL=http://api-service:5055
    // Render Blueprints expose private services as host:port (without a scheme).
    const internalApiUrl = process.env.INTERNAL_API_URL ||
      (process.env.INTERNAL_API_HOSTPORT
        ? `http://${process.env.INTERNAL_API_HOSTPORT}`
        : 'http://127.0.0.1:5055')

    console.log(`[Next.js Rewrites] Proxying /api/* to ${internalApiUrl}/api/*`)

    return [
      {
        source: '/api/:path*',
        destination: `${internalApiUrl}/api/:path*`,
      },
    ]
  },
};

export default nextConfig;
