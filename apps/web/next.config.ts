import type { NextConfig } from "next";
import { assertTorreApiConfig, torreApiRewrites } from "./src/lib/torre-api-proxy.ts";

const nextConfig: NextConfig = {
  async rewrites() {
    assertTorreApiConfig(process.env.NEXT_PUBLIC_API_URL, process.env.TORRE_API_ORIGIN);
    return torreApiRewrites(process.env.TORRE_API_ORIGIN);
  },
};

export default nextConfig;
