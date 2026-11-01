# ADR-0009: SPA served by the API

Status: accepted

The dashboard is a Vite-built React SPA. In local and production deployments FastAPI serves `web/dist` (one origin,
no CORS, one port; strict CSP). During UI development Vite proxies `/api` to the backend. No state management
library or component framework: a small set of local components and a polling data hook keep dependencies minimal.
