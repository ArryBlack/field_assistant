# Field Assistant

## Overview

Field Assistant is a Docker Compose monorepo that runs a MongoDB-backed backend, a long-running ingestion worker, and a React + Vite frontend.

This repository is structured as:

- `docker-compose.yml` — runtime orchestration for `mongodb`, `backend-api`, `fetcher`, and `frontend`
- `packages/backend/` — Python backend code, worker, Dockerfile, and service helpers
- `packages/frontend/` — React + Vite frontend app, Dockerfile, and production Nginx config
- `media_data/` — local media storage mounted into backend containers
- `mongo_data/` — MongoDB data files persisted outside containers

---

## Architecture

### Services

- `mongodb`
  - Image: `mongo:7.0`
  - Stores all application data for users, messages, media, and trips
  - Data persisted to `./mongo_data`

- `backend-api`
  - Built from `packages/backend/Dockerfile`
  - Runs the FastAPI application from `packages/backend/api.py`
  - Exposes port `5002` on the host, mapped to `8000` in the container
  - Mounts `./packages/backend` and `./media_data`

- `fetcher`
  - Uses the same backend image as `backend-api`
  - Starts `packages/backend/fetcher.py` via `packages/backend/start.sh fetcher`
  - Handles Telegram ingestion and background media processing

- `frontend`
  - Built from `packages/frontend/Dockerfile`
  - Serves a static React + Vite application using Nginx on port `5003`
  - Injects `VITE_API_URL` at build time so the frontend knows where the backend is located

---

## Backend

The backend lives in `packages/backend/` and consists of:

- `api.py` — main FastAPI application, route definitions, and AI/media endpoints
- `fetcher.py` — long-running ingestion worker, polling Telegram and saving media/messages
- `Dockerfile` — builds the Python runtime image and installs dependencies
- `start.sh` — entrypoint script with three supported modes:
  - `api` — run only the API server
  - `fetcher` — run only the ingestion worker
  - `both` — start fetcher in background and API in foreground

### Backend runtime details

- API server is launched with Gunicorn + Uvicorn
- Fetcher runs as a standalone Python worker
- Local media files are stored under `/app/media` inside the container, mapped from `./media_data`

---

## Frontend

The frontend lives in `packages/frontend/` and includes:

- `package.json` — scripts, React, TypeScript, and build dependencies
- `vite.config.ts` — Vite configuration for the app
- `nginx.conf` — production Nginx config used in the Docker image
- `src/` — React app source files

### Build and deploy flow

- Build stage uses Node 18 Alpine and runs `npm ci` + `npm run build`
- Production stage uses `nginx:stable-alpine`
- `VITE_API_URL` is injected as a Docker build argument and becomes available to Vite as the backend base URL

---

## Local development

### Start the full stack

```bash
docker compose up --build
```

This starts all services:
- `mongodb`
- `backend-api`
- `fetcher`
- `frontend`

### Stop the stack

```bash
docker compose down
```

### Start individual services

```bash
docker compose up --build backend-api fetcher frontend mongodb
```

If you want to run only the API or only the fetcher, use the `command` override or edit `packages/backend/start.sh` mode behavior in `docker-compose.yml`.

---

## Data persistence

- `media_data/` stores media files written by the backend and fetcher
- `mongo_data/` stores MongoDB database files for persistence across restarts

---

## Environment variables

The stack expects a root `.env` file with values for the backend and frontend.

Common variables include:

- `MONGO_USER`
- `MONGO_PASS`
- `MONGO_URI`
- `GOOGLE_API_KEY`
- `VITE_API_URL`
- `LOCAL_MEDIA_PATH`
- `WEB_CONCURRENCY`

The `backend-api` and `fetcher` services read `env_file: .env` from the root.
The frontend build reads `VITE_API_URL` from the Docker build args.

---

## Troubleshooting

- If the frontend reports `Cannot find module 'react' or its corresponding type declarations`, make sure dependencies are installed inside `packages/frontend` and the container image is rebuilt.
- If the backend cannot connect, confirm MongoDB is running and `MONGO_URI` is correct.
- If media files are missing, verify `media_data/` is mounted correctly in the backend and fetcher services.

---

## Useful file locations

- `docker-compose.yml` — container orchestration and service definitions
- `packages/backend/Dockerfile` — backend image build logic
- `packages/backend/start.sh` — backend service startup modes
- `packages/backend/api.py` — FastAPI application and REST endpoints
- `packages/backend/fetcher.py` — Telegram ingestion worker
- `packages/frontend/Dockerfile` — frontend build and Nginx production image
- `packages/frontend/package.json` — frontend package metadata and scripts
- `packages/frontend/nginx.conf` — Nginx config for SPA routing and static hosting

---

## Notes

This README reflects the current repository layout and deployment flow, not a prior design with PostgreSQL or S3-based storage.
