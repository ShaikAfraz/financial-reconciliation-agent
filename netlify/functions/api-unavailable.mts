import type { Config } from "@netlify/functions";

// The reconciliation API is served by the Python backend (server.py), which
// does not run on Netlify. Return a clear JSON error instead of an HTML 404.
export default async () =>
  Response.json(
    {
      error:
        "The reconciliation API is not available on this deployment. Run `python server.py` locally to use the full application.",
    },
    { status: 503 }
  );

export const config: Config = {
  path: "/api/*",
};
