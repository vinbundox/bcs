import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.main import app, hash_password, verify_password


class TimberTrackTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

        def override_db():
            db = self.sessions()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.csrf = self.client.get("/api/v1/auth/session").json()["csrf_token"]

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def headers(self):
        return {"X-CSRF-Token": self.csrf}

    def setup_owner(self):
        response = self.client.post(
            "/api/v1/auth/setup",
            json={"username": "owner", "password": "a sufficiently long password"},
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_password_hash_is_salted_and_legacy_compatible(self):
        stored = hash_password("correct horse battery")
        self.assertNotEqual(stored, hash_password("correct horse battery"))
        self.assertTrue(verify_password("correct horse battery", stored))
        self.assertFalse(verify_password("wrong password", stored))

    def test_jwt_auth_stock_traceability_reports_and_rbac(self):
        self.assertEqual(self.client.get("/api/v1/suppliers").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/setup", json={
            "username": "owner", "password": "a sufficiently long password",
        }).status_code, 403)
        self.setup_owner()
        self.assertTrue(self.client.get("/api/v1/auth/me").json()["username"] == "owner")

        supplier = self.client.post("/api/v1/suppliers", json={"name": "Forest Supply"}, headers=self.headers())
        self.assertEqual(supplier.status_code, 201, supplier.text)
        purchase_order = self.client.post("/api/v1/purchase-orders", json={
            "order_code": "PO-001", "supplier_id": supplier.json()["id"], "notes": "First delivery",
        }, headers=self.headers())
        self.assertEqual(purchase_order.status_code, 201, purchase_order.text)
        lot = self.client.post(f"/api/v1/purchase-orders/{purchase_order.json()['id']}/receive", json={
            "lot_code": "LOT-1", "species": "Cypress", "grade": "A",
            "volume_m3": 5, "cost_per_m3": 1000, "location": "Bay A",
            "dimensions": "50x100mm", "pieces": 20,
        }, headers=self.headers())
        self.assertEqual(lot.status_code, 201, lot.text)
        lot_id = lot.json()["id"]
        self.assertEqual(self.client.get("/api/v1/purchase-orders").json()[0]["status"], "received")

        customer = self.client.post("/api/v1/customers", json={"name": "Customer", "phone": "0700000000"},
                                    headers=self.headers())
        self.assertEqual(customer.status_code, 201, customer.text)
        sales_order = self.client.post("/api/v1/sales-orders", json={
            "order_code": "SO-001", "customer_id": customer.json()["id"],
        }, headers=self.headers())
        self.assertEqual(sales_order.status_code, 201, sales_order.text)
        location = self.client.post("/api/v1/locations", json={"name": "Bay B"}, headers=self.headers())
        self.assertEqual(location.status_code, 201, location.text)

        reserved = self.client.post(f"/api/v1/lots/{lot_id}/movements", json={"kind": "reserve"},
                                    headers=self.headers())
        self.assertEqual(reserved.status_code, 200, reserved.text)
        held_sale = self.client.post("/api/v1/sales", json={
            "lot_id": lot_id, "customer": "Customer", "volume_m3": 1, "price_per_m3": 1800,
        }, headers=self.headers())
        self.assertEqual(held_sale.status_code, 409)
        released = self.client.post(f"/api/v1/lots/{lot_id}/movements", json={"kind": "unreserve"},
                                    headers=self.headers())
        self.assertEqual(released.status_code, 200, released.text)
        sale = self.client.post("/api/v1/sales", json={
            "lot_id": lot_id, "customer": "Customer", "volume_m3": 2, "price_per_m3": 1800,
        }, headers=self.headers())
        self.assertEqual(sale.status_code, 201, sale.text)
        over_sale = self.client.post("/api/v1/sales", json={
            "lot_id": lot_id, "customer": "Customer", "volume_m3": 4, "price_per_m3": 1800,
        }, headers=self.headers())
        self.assertEqual(over_sale.status_code, 409)

        reports = self.client.get("/api/v1/reports").json()
        self.assertEqual(reports["summary"]["stock_m3"], 3)
        self.assertEqual(reports["summary"]["sold_m3"], 2)
        self.assertEqual(reports["financial"]["sales_kes"], 3600)
        performance = self.client.get("/api/v1/reports/supplier-performance").json()
        self.assertEqual(performance[0]["purchased_m3"], 5)
        purchases = self.client.get("/api/v1/reports/purchases-vs-sales?from=2026-01-01&to=2027-01-01").json()
        self.assertEqual(purchases["sold_m3"], 2)
        margin = self.client.get("/api/v1/reports/margin-by-species").json()
        self.assertEqual(margin[0]["estimated_margin_kes"], 1600)
        stock = self.client.get("/api/v1/reports/stock").json()
        self.assertEqual(stock["by_location"][0]["location"], "Bay A")
        trace = self.client.get("/api/v1/reports/traceability/LOT-1").json()
        self.assertEqual(trace["lot"]["dimensions"], "50x100mm")
        history = self.client.get(f"/api/v1/lots/{lot_id}/history").json()
        self.assertEqual([entry["kind"] for entry in history], ["received", "reserve", "unreserve", "sold"])

        team = self.client.post("/api/v1/users", json={
            "username": "yardstaff", "password": "another long password", "role": "yard",
        }, headers=self.headers())
        self.assertEqual(team.status_code, 201, team.text)
        self.client.delete("/api/v1/auth/session", headers=self.headers())
        self.csrf = self.client.get("/api/v1/auth/session").json()["csrf_token"]
        login = self.client.post("/api/v1/auth/login", json={
            "username": "yardstaff", "password": "another long password",
        }, headers=self.headers())
        self.assertEqual(login.status_code, 200, login.text)
        self.csrf = self.client.get("/api/v1/auth/session").json()["csrf_token"]
        yard_report = self.client.get("/api/v1/reports").json()
        self.assertNotIn("financial", yard_report)
        self.assertEqual(self.client.get("/api/v1/audit").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/sales", json={
            "lot_id": lot_id, "customer": "Customer", "volume_m3": 1, "price_per_m3": 100,
        }, headers=self.headers()).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

    def test_idempotent_alembic_upgrade_preserves_existing_sqlite_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.db"
            legacy = sqlite3.connect(path)
            legacy.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner','sourcing','yard','sales','finance','viewer')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE suppliers (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE lots (
                    id INTEGER PRIMARY KEY, lot_code TEXT NOT NULL UNIQUE,
                    supplier_id INTEGER NOT NULL REFERENCES suppliers(id), species TEXT NOT NULL,
                    grade TEXT NOT NULL DEFAULT '', volume_m3 REAL NOT NULL, remaining_m3 REAL NOT NULL,
                    cost_per_m3 REAL NOT NULL, location TEXT NOT NULL,
                    origin_document TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                INSERT INTO users VALUES (1,'legacy','legacy-hash','owner','2026-01-01T00:00:00+00:00');
                INSERT INTO suppliers VALUES (1,'Legacy supplier','','2026-01-01T00:00:00+00:00');
                INSERT INTO lots VALUES (1,'LEGACY-1',1,'Cypress','A',4,4,50,'Bay A','','2026-01-01T00:00:00+00:00');
            """)
            legacy.close()
            url = f"sqlite:///{path.as_posix()}"
            config = Config("backend/alembic.ini")
            with patch.dict(os.environ, {"DATABASE_URL": url}):
                command.upgrade(config, "head")
                command.upgrade(config, "head")

            engine = create_engine(url)
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql("SELECT username FROM users WHERE id=1").scalar(), "legacy")
                self.assertEqual(connection.exec_driver_sql("SELECT lot_code FROM lots WHERE id=1").scalar(), "LEGACY-1")
                self.assertIn("dimensions", {column["name"] for column in inspect(connection).get_columns("lots")})
                self.assertIn("is_active", {column["name"] for column in inspect(connection).get_columns("users")})
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
