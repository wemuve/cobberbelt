# Copperbelt Intelligence web app

Next.js 15 + React 19 + MapLibre. Evidence viewer, search, map with true-scale location uncertainty, projects, assessments and admin. See the [root README](../README.md) for the full stack.

```bash
npm ci
npm run dev          # http://localhost:3000, expects the API at NEXT_PUBLIC_API_URL (default http://localhost:8000)
npm test             # unit tests (Vitest)
npm run lint
npm run typecheck
npm run build
```

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API base URL (baked in at build time) |
| `NEXT_PUBLIC_MAP_STYLE_URL` | MapLibre demo tiles | Basemap style JSON |

Browser journey test against a running stack: `WEB=http://localhost:3000 FIXTURE=path/to/synthetic.pdf CHROME=/path/to/chrome node e2e/journey.mjs` (screenshots go to `$SHOTS`, default `e2e-shots`).
