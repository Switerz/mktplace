import type { NextConfig } from "next";
import { torreApiRewrites } from "./src/lib/torre-api-proxy.ts";

const nextConfig: NextConfig = {
  async rewrites() {
    return torreApiRewrites(process.env.TORRE_API_ORIGIN);
  },
};

export default nextConfig;
