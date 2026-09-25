import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Next 16's dev server serves /_next/* only to hosts it recognises, and treats 127.0.0.1 as a
  // different origin from localhost. Without this, opening the app on the IP loads the HTML but
  // NOT the client bundle: the page renders its static shell, hydration never runs, and every
  // data panel sits empty with nothing in the console to explain why. Dev-only setting.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // The API is a separate FastAPI process. Proxying it under /api keeps the browser on one
  // origin, so there is no CORS preflight on every request and no API URL baked into the bundle.
  async rewrites() {
    const target = process.env.API_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
