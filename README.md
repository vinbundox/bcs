# TimberTrack

TimberTrack is a mobile-first installable web app with a separate React/Vite frontend and FastAPI backend. It tracks suppliers, purchase orders, timber lots, locations and movements, sales, reporting, team roles and audit history. Stock is categorized as timber, poles, posts, or others; timber and others use a selectable ft³/m³ display (ft³ by default), while poles and posts are counted in pieces. Backend data is stored in SQLite with SQLAlchemy and versioned by Alembic.

## Run the full stack with Docker

Requirements: Docker Engine/Compose, a hostname whose DNS points to the server, and inbound TCP ports 80 and 443 open on the server.

1. Copy the project to the server and create a local `.env` file:

   ```sh
   cp .env.example .env
   ```

2. Set `APP_DOMAIN` in `.env` to your real hostname. Replace the example JWT signing key with a unique random value:

   ```sh
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

3. Build and launch the separate backend, React frontend, and HTTPS proxy:

   ```sh
   docker compose up -d --build
   docker compose ps
   ```

4. Open `https://your-hostname` on a phone, create the owner account, then use the browser's **Install app** / **Add to Home Screen** option.

Caddy provisions and renews HTTPS certificates automatically. It routes `/api/*` to FastAPI and the app interface to Nginx. The API's interactive OpenAPI page is at `https://your-hostname/api/v1/docs`. Only Caddy publishes internet-facing ports; the backend and frontend stay on the private Compose network. HTTPS session cookies are enabled in the Docker deployment.

For a browser check on the Docker host itself, the frontend is also bound to `127.0.0.1:8766` by default (or `PREVIEW_PORT` in `.env`). This loopback-only port is not reachable from other machines; phones should use the HTTPS hostname.

## Deploy a single service to Render

The root `Dockerfile` builds the React app and serves it from FastAPI, so a Docker-based Render web service can host the frontend and API on one origin:

1. In Render, create a **Web Service** from this repository and select the **Docker** runtime. Set the Dockerfile path to `./Dockerfile` and the build context to the repository root.
2. Set the health check path to `/healthz`.
3. Add `JWT_SECRET_KEY` with a unique random value, and set `TIMBER_COOKIE_SECURE=1`.
4. Deploy and open the service URL. The app should load at `/`; API documentation is at `/api/v1/docs`.

The Dockerfile listens on Render's `PORT` environment variable (and defaults to port 8000 for local Docker). A Python/native Render service that only starts Uvicorn will serve the API but not the React app. Render free services have ephemeral local storage, so SQLite data is only suitable for disposable tests and may be lost after restarts or redeploys. Use an approved persistent database/storage plan before entering real business data.

SQLite is stored in the `timber-data` Docker volume and survives app image rebuilds. The startup Alembic migration is designed to add the new schema fields to the original SQLite database without deleting existing rows. Keep independent backups and test restoring them. Do not remove the data volume when updating. To deploy code updates, copy the new project files to the server and run:

```sh
docker compose up -d --build
```

Services and logs:

```sh
docker compose ps
docker compose logs -f backend frontend caddy
```

## Local development

### Backend API

Requires Python 3.10+.

```sh
python -m pip install -r requirements.txt
python app.py
```

The backend runs at `http://127.0.0.1:8000`; its API docs are at `http://127.0.0.1:8000/api/v1/docs`. Alembic applies database migrations at startup. By default, local development uses `timber.db` in this folder; configure `TIMBER_DB` or `DATABASE_URL` to use another database.

### React mobile frontend

Requires Node.js 20+.

```sh
cd frontend
npm install
npm run dev
```

Vite runs at `http://localhost:5173` and proxies `/api` requests to the local backend on port 8000.

Run API and migration tests with:

```sh
python -m unittest discover -s tests -v
```

## API areas

- **Authentication:** `/api/v1/auth/login`, `/auth/setup`, `/auth/me`; signed JWTs are kept in HTTP-only cookies, with CSRF protection on writes.
- **Users and roles:** `/api/v1/users` supports owner-managed user creation, updates and deactivation. Roles are `owner`, `sourcing`, `yard`, `sales`, `finance` and `viewer`.
- **Suppliers and purchasing:** `/api/v1/suppliers`, `/api/v1/purchase-orders`, and purchase receipt endpoints.
- **Timber traceability:** `/api/v1/lots`, `/api/v1/lots/{id}/movements`, `/api/v1/lots/{id}/history`, and lot-code traceability reports. Owners can correct received purchase details; received volume remains unchanged.
- **Stock and locations:** `/api/v1/locations` and `/api/v1/reports/stock`.
- **Customers and sales:** `/api/v1/customers`, `/api/v1/sales`, and `/api/v1/sales-orders`.
- **Reporting and audit:** `/api/v1/reports`, date-filtered purchasing/sales and margin reports, supplier performance, gross profit/loss by species and lot, available-stock valuation at recorded buying rates, PNG downloads and PDF print views for reports, inventory and sale records.

## Current scope

This is an early operational MVP, not production-ready financial or tax software. Owners can edit recorded sale/customer details and selling rates; changing a sale quantity adjusts available stock and every change is recorded in the audit history. Profit/loss reports show estimated gross profit across categories (sales less recorded purchase cost); operating expenses, taxes, and other overheads are not included. PDF downloads open the browser print dialog; choose **Save as PDF**. Sale PDFs are sales records and do not calculate VAT or other taxes; tax-compliant invoices, payments, document upload, QR/barcode scanning, CSV export and offline data synchronization still need to be added. Reports are operational summaries rather than audited accounts. Existing quantities remain stored in their original base values; cubic-metre stock is converted for display rather than rewritten. Money is tracked in Kenyan shillings.

The app is intended for a small yard and initial evaluation. Before relying on it for business-critical data, arrange monitoring, backups and recovery drills. Use a unique JWT key and HTTPS on any shared deployment.
