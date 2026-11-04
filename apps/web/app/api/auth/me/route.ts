import { handleMe } from "../../../../src/server/auth/handlers.ts";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function currentEnv() {
  return {
    AUTH_GOOGLE_ID: process.env.AUTH_GOOGLE_ID,
    AUTH_SECRET: process.env.AUTH_SECRET,
    ALLOWED_EMAIL_DOMAINS: process.env.ALLOWED_EMAIL_DOMAINS,
    NODE_ENV: process.env.NODE_ENV,
  };
}

export async function GET(req: Request): Promise<Response> {
  return handleMe(req, currentEnv());
}
