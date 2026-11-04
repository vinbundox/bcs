# TimberTrack

TimberTrack is a mobile-first installable web app with a separate React/Vite frontend and FastAPI backend. It tracks suppliers, purchase orders, timber lots, locations and movements, sales, reporting, team roles and audit history. Backend data is stored in SQLite with SQLAlchemy and versioned by Alembic.

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
- **Timber traceability:** `/api/v1/lots`, `/api/v1/lots/{id}/movements`, `/api/v1/lots/{id}/history`, and lot-code traceability reports.
- **Stock and locations:** `/api/v1/locations` and `/api/v1/reports/stock`.
- **Customers and sales:** `/api/v1/customers`, `/api/v1/sales`, and `/api/v1/sales-orders`.
- **Reporting and audit:** `/api/v1/reports`, date-filtered purchasing/sales and margin reports, supplier performance, and owner-only audit history.

## Current scope

This is an early operational MVP, not production-ready financial or tax software. Sales are dispatch records; tax-compliant invoices, payments, document upload, QR/barcode scanning, CSV/PDF export and offline data synchronization still need to be added. Reports are operational summaries rather than audited accounts. Volume is tracked in cubic metres and money in Kenyan shillings.

The app is intended for a small yard and initial evaluation. Before relying on it for business-critical data, arrange monitoring, backups and recovery drills. Use a unique JWT key and HTTPS on any shared deployment.
