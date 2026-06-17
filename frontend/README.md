# NV Battle Reports — Frontend

React 18 + Vite 6 + TypeScript SPA for the NV Battle Reports tool.

## Development

Start the backend first:

```bash
DEV_MODE=1 uv run uvicorn app.main:app --port 8000
```

Then run the Vite dev server (proxies `/api` and `/healthz` to the backend):

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## Production / Demo

Build the SPA, then the FastAPI server serves both API and SPA at the same origin:

```bash
cd frontend && npm run build
DEV_MODE=1 uv run uvicorn app.main:app --port 8000
```

Open http://localhost:8000.

## Tests

```bash
cd frontend && npm test
```

## URL Prefix

Set `VITE_URL_PREFIX=/your-prefix` before building when deploying under a sub-path behind NV Tools:

```bash
VITE_URL_PREFIX=/nvbr npm run build
```

The dev proxy will mirror the same prefix. The `nv_embed.js` script in `<head>` uses an absolute URL and is never prefixed — this is a hard contract requirement.
