# AgentPanelX Web Console

This directory contains the local React console. It talks to the FastAPI host through
same-origin `/api` paths; during development Vite proxies those requests to
`http://127.0.0.1:13475`.

From the repository root, start the backend:

```bash
uv run agentplanex-web
```

In a second terminal, start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The Project Owner model gateway is not required to inspect
projects, create features, read the board, open a workspace, or submit a command receipt.
If the gateway is unavailable, the console reports the accepted activation or backend
failure without creating a fake Owner response.

Quality checks:

```bash
npm run check
npm run lint
npm run build
```

After `npm run build`, starting `uv run agentplanex-web` from the repository root also
serves the built console at `http://127.0.0.1:13475`. Use
`--frontend-dist /path/to/dist` when starting the backend from another directory.
