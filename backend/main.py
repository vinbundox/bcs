import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import AuditLog, Customer, Lot, Location, Movement, PurchaseOrder, Sale, SalesOrder, Supplier, User
from backend.schemas import (
    Credentials, CustomerCreate, LocationCreate, LotCreate, MoveRequest, PurchaseOrderCreate,
    MovementCreate, PurchaseOrderReceive, SaleCreate, SalesOrderCreate, SetupCredentials,
    SupplierCreate, UserCreate, UserPatch,
)


JWT_SECRET = os.environ.get("JWT_SECRET_KEY", "local-development-key-change-before-deploy-32chars")
JWT_ALGORITHM = "HS256"
SESSION_SECONDS = 12 * 60 * 60
SECURE_COOKIE = os.environ.get("TIMBER_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
if SECURE_COOKIE and (
    len(JWT_SECRET) < 32 or JWT_SECRET.startswith("replace") or JWT_SECRET == "local-development-key-change-before-deploy-32chars"
):
    raise RuntimeError("Set a unique JWT_SECRET_KEY for the HTTPS deployment.")
ROLE_PERMISSIONS = {
    "owner": {"*"},
    "sourcing": {"suppliers.read", "suppliers.write", "lots.read", "lots.write", "reports.read"},
    "yard": {"lots.read", "lots.write", "lots.move", "reports.read"},
    "sales": {"lots.read", "sales.read", "sales.write", "reports.read"},
    "finance": {"lots.read", "sales.read", "reports.read", "costs.read"},
    "viewer": {"lots.read", "sales.read", "reports.read"},
}
SESSION_COOKIE = "timber_session"
CSRF_COOKIE = "timber_csrf"
Db = Annotated[Session, Depends(get_db)]

app = FastAPI(
    title="TimberTrack API",
    description="Mobile timber sourcing, inventory, traceability and sales API.",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
        candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, digest_hex)


def create_access_token(user: User) -> str:
    issued = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": str(user.id),
        "role": user.role,
        "iat": issued,
        "exp": issued + timedelta(seconds=SESSION_SECONDS),
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)


def current_user(request: Request, db: Db) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(claims["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has expired. Please sign in again.")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    return user


def require_permission(permission: str):
    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role != "owner" and permission not in ROLE_PERMISSIONS.get(user.role, set()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do that.")
        return user
    return check


def require_csrf(
    request: Request,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    cookie_value = request.cookies.get(CSRF_COOKIE, "")
    if not cookie_value or not csrf_header or not hmac.compare_digest(cookie_value, csrf_header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Refresh the app and try again.")


def audit(db: Session, user: User, action: str, entity: str, entity_id: int | str, detail: str = ""):
    db.add(AuditLog(
        user_id=user.id,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        detail=detail,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_login_cookie(response: Response, user: User):
    response.set_cookie(
        SESSION_COOKIE,
        create_access_token(user),
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=SECURE_COOKIE,
        samesite="strict",
        path="/",
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "That record conflicts with existing data."})


@app.get("/healthz", tags=["health"])
def health(db: Db):
    db.execute(select(1))
    return {"ok": True}


@app.get("/api/v1/auth/session", tags=["auth"])
def session_info(request: Request, response: Response, db: Db):
    csrf_token = secrets.token_urlsafe(24)
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    if token:
        try:
            claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            record = db.get(User, int(claims["sub"]))
            if record and record.is_active:
                user = {"id": record.id, "username": record.username, "role": record.role}
        except (JWTError, KeyError, TypeError, ValueError):
            user = None
    response.set_cookie(CSRF_COOKIE, csrf_token, max_age=SESSION_SECONDS, secure=SECURE_COOKIE,
                        httponly=False, samesite="strict", path="/")
    return {
        "csrf_token": csrf_token,
        "setup_required": db.scalar(select(func.count(User.id))) == 0,
        "user": user,
        "permissions": sorted(ROLE_PERMISSIONS[user["role"]]) if user else [],
    }


@app.get("/api/v1/auth/me", tags=["auth"])
def auth_me(user: Annotated[User, Depends(current_user)]):
    return {"id": user.id, "username": user.username, "role": user.role}


@app.post("/api/v1/auth/setup", status_code=status.HTTP_201_CREATED, tags=["auth"], dependencies=[Depends(require_csrf)])
def initial_setup(data: SetupCredentials, response: Response, db: Db):
    if db.scalar(select(func.count(User.id))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup is already complete.")
    user = User(username=data.username.strip(), password_hash=hash_password(data.password), role="owner",
                created_at=timestamp())
    db.add(user)
    db.flush()
    audit(db, user, "create", "user", user.id, "Initial owner")
    db.commit()
    set_login_cookie(response, user)
    return {"ok": True}


@app.post("/api/v1/auth/login", tags=["auth"], dependencies=[Depends(require_csrf)])
def login(data: Credentials, response: Response, db: Db):
    user = db.scalar(select(User).where(User.username == data.username.strip()))
    if not user or not user.is_active or not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Username or password is incorrect.")
    set_login_cookie(response, user)
    return {"ok": True}


@app.delete("/api/v1/auth/session", tags=["auth"], dependencies=[Depends(require_csrf)])
def logout(response: Response, user: Annotated[User, Depends(current_user)], db: Db):
    audit(db, user, "sign_out", "session", user.id)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=SECURE_COOKIE, samesite="strict")
    return {"ok": True}


@app.get("/api/v1/suppliers", tags=["suppliers"])
def list_suppliers(_user: Annotated[User, Depends(require_permission("suppliers.read"))], db: Db):
    rows = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    return [{"id": row.id, "name": row.name, "phone": row.phone, "created_at": row.created_at} for row in rows]


@app.post("/api/v1/suppliers", status_code=status.HTTP_201_CREATED, tags=["suppliers"],
          dependencies=[Depends(require_csrf)])
def create_supplier(data: SupplierCreate, user: Annotated[User, Depends(require_permission("suppliers.write"))], db: Db):
    supplier = Supplier(name=data.name.strip(), phone=data.phone.strip(), created_at=timestamp())
    db.add(supplier)
    db.flush()
    audit(db, user, "create", "supplier", supplier.id, supplier.name)
    db.commit()
    return {"id": supplier.id}


def serialize_lot(lot: Lot, include_cost: bool):
    remaining = float(lot.remaining_m3)
    status_label = "Sold" if remaining == 0 else "Partially sold" if remaining < float(lot.volume_m3) else "In stock"
    result = {
        "id": lot.id, "lot_code": lot.lot_code, "supplier_id": lot.supplier_id,
        "supplier_name": lot.supplier.name, "species": lot.species, "grade": lot.grade,
        "volume_m3": float(lot.volume_m3), "remaining_m3": remaining, "location": lot.location,
        "origin_document": lot.origin_document, "created_at": lot.created_at, "status": status_label,
        "dimensions": lot.dimensions, "pieces": lot.pieces, "lot_status": lot.status,
        "purchase_order_id": lot.purchase_order_id,
    }
    if include_cost:
        result["cost_per_m3"] = float(lot.cost_per_m3)
    return result


@app.get("/api/v1/lots", tags=["lots"])
def list_lots(user: Annotated[User, Depends(require_permission("lots.read"))], db: Db):
    lots = db.scalars(select(Lot).order_by(Lot.created_at.desc())).all()
    include_cost = user.role in {"owner", "finance"}
    return [serialize_lot(lot, include_cost) for lot in lots]


@app.post("/api/v1/lots", status_code=status.HTTP_201_CREATED, tags=["lots"],
          dependencies=[Depends(require_csrf)])
def receive_lot(data: LotCreate, user: Annotated[User, Depends(require_permission("lots.write"))], db: Db):
    if not db.get(Supplier, data.supplier_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found.")
    if data.purchase_order_id and not db.get(PurchaseOrder, data.purchase_order_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found.")
    lot = Lot(
        **data.model_dump(), remaining_m3=data.volume_m3, status="in_stock", created_at=timestamp(),
    )
    db.add(lot)
    db.flush()
    db.add(Movement(lot_id=lot.id, kind="received", detail=f"Received at {lot.location}",
                    volume_m3=lot.volume_m3, user_id=user.id, created_at=timestamp()))
    if lot.purchase_order_id:
        order = db.get(PurchaseOrder, lot.purchase_order_id)
        order.status = "received"
    audit(db, user, "receive", "lot", lot.id, f"{lot.lot_code}: {lot.volume_m3} m3")
    db.commit()
    return {"id": lot.id}


@app.get("/api/v1/lots/{lot_id}/history", tags=["lots"])
def lot_history(lot_id: int, _user: Annotated[User, Depends(require_permission("lots.read"))], db: Db):
    lot = db.get(Lot, lot_id)
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found.")
    rows = db.scalars(select(Movement).where(Movement.lot_id == lot_id).order_by(Movement.id)).all()
    return [{
        "kind": row.kind, "detail": row.detail, "volume_m3": row.volume_m3,
        "created_at": row.created_at, "username": row.user.username,
    } for row in rows]


@app.post("/api/v1/lots/{lot_id}/move", tags=["lots"], dependencies=[Depends(require_csrf)])
def move_lot(lot_id: int, data: MoveRequest,
             user: Annotated[User, Depends(require_permission("lots.move"))], db: Db):
    lot = db.get(Lot, lot_id)
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found.")
    location = data.location.strip()
    if not location:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "New location is required.")
    lot.location = location
    db.add(Movement(lot_id=lot.id, kind="moved", detail=f"Moved to {location}",
                    volume_m3=lot.remaining_m3, user_id=user.id, created_at=timestamp()))
    audit(db, user, "move", "lot", lot.id, location)
    db.commit()
    return {"ok": True}


@app.post("/api/v1/lots/{lot_id}/movements", tags=["lots"], dependencies=[Depends(require_csrf)])
def create_movement(lot_id: int, data: MovementCreate,
                    user: Annotated[User, Depends(require_permission("lots.move"))], db: Db):
    lot = db.get(Lot, lot_id)
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found.")
    detail = data.detail.strip()
    if data.kind == "move":
        if not data.location or not data.location.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A new location is required.")
        lot.location = data.location.strip()
        detail = detail or f"Moved to {lot.location}"
    elif data.kind == "reserve":
        if lot.status != "in_stock":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only in-stock timber can be reserved.")
        lot.status = "reserved"
        detail = detail or "Reserved"
    elif data.kind == "unreserve":
        if lot.status != "reserved":
            raise HTTPException(status.HTTP_409_CONFLICT, "This lot is not reserved.")
        lot.status = "in_stock"
        detail = detail or "Reservation released"
    elif data.kind == "graded":
        if lot.status not in {"in_stock", "received", "graded"}:
            raise HTTPException(status.HTTP_409_CONFLICT, "This lot can no longer be graded.")
        lot.status = "graded"
        detail = detail or f"Graded {lot.grade}"
    elif data.kind == "delivered":
        if lot.remaining_m3 > 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "Deliver the remaining stock before completing this lot.")
        lot.status = "delivered"
        detail = detail or "Delivery completed"
    movement = Movement(
        lot_id=lot.id, kind=data.kind, detail=detail or data.kind.title(),
        volume_m3=lot.remaining_m3, user_id=user.id, created_at=timestamp(),
    )
    db.add(movement)
    audit(db, user, data.kind, "lot", lot.id, movement.detail)
    db.commit()
    return {"id": movement.id, "status": lot.status, "location": lot.location}


@app.get("/api/v1/sales", tags=["sales"])
def list_sales(_user: Annotated[User, Depends(require_permission("sales.read"))], db: Db):
    sales = db.scalars(select(Sale).order_by(Sale.created_at.desc()).limit(100)).all()
    return [{
        "id": sale.id, "customer": sale.customer, "volume_m3": sale.volume_m3,
        "price_per_m3": sale.price_per_m3, "created_at": sale.created_at,
        "lot_code": sale.lot.lot_code, "species": sale.lot.species, "recorded_by": sale.user.username,
    } for sale in sales]


@app.post("/api/v1/sales", status_code=status.HTTP_201_CREATED, tags=["sales"],
          dependencies=[Depends(require_csrf)])
def create_sale(data: SaleCreate, user: Annotated[User, Depends(require_permission("sales.write"))], db: Db):
    lot = db.get(Lot, data.lot_id)
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found.")
    if data.volume_m3 > lot.remaining_m3:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sale volume exceeds available stock.")
    if lot.status == "reserved":
        raise HTTPException(status.HTTP_409_CONFLICT, "This lot is reserved and cannot be sold.")
    updated = db.execute(
        update(Lot).where(Lot.id == data.lot_id, Lot.remaining_m3 >= data.volume_m3)
        .values(remaining_m3=Lot.remaining_m3 - data.volume_m3)
    )
    if not updated.rowcount:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Sale volume exceeds available stock.")
    sale = Sale(lot_id=data.lot_id, customer=data.customer.strip(), volume_m3=data.volume_m3,
                price_per_m3=data.price_per_m3, user_id=user.id, created_at=timestamp())
    db.add(sale)
    db.flush()
    db.add(Movement(lot_id=data.lot_id, kind="sold",
                    detail=f"Sold to {data.customer.strip()} (sale {sale.id})",
                    volume_m3=data.volume_m3, user_id=user.id, created_at=timestamp()))
    audit(db, user, "sell", "lot", lot.id, f"{lot.lot_code}: {data.volume_m3} m3 to {data.customer.strip()}")
    db.commit()
    return {"id": sale.id}


@app.get("/api/v1/users", tags=["users"])
def list_users(_user: Annotated[User, Depends(require_permission("users.manage"))], db: Db):
    users = db.scalars(select(User).order_by(User.username)).all()
    return [{"id": row.id, "username": row.username, "role": row.role, "created_at": row.created_at,
             "is_active": row.is_active} for row in users]


@app.post("/api/v1/users", status_code=status.HTTP_201_CREATED, tags=["users"],
          dependencies=[Depends(require_csrf)])
def create_user(data: UserCreate, user: Annotated[User, Depends(require_permission("users.manage"))], db: Db):
    record = User(username=data.username.strip(), password_hash=hash_password(data.password),
                  role=data.role, created_at=timestamp())
    db.add(record)
    db.flush()
    audit(db, user, "create", "user", record.id, f"{record.username} ({record.role})")
    db.commit()
    return {"id": record.id}


@app.get("/api/v1/users/{user_id}", tags=["users"])
def get_user(user_id: int, _user: Annotated[User, Depends(require_permission("users.manage"))], db: Db):
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return {"id": record.id, "username": record.username, "role": record.role,
            "created_at": record.created_at, "is_active": record.is_active}


@app.patch("/api/v1/users/{user_id}", tags=["users"], dependencies=[Depends(require_csrf)])
def update_user(user_id: int, data: UserPatch,
                actor: Annotated[User, Depends(require_permission("users.manage"))], db: Db):
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    values = data.model_dump(exclude_unset=True)
    if values.get("username") is not None:
        record.username = values["username"].strip()
    if values.get("role") is not None:
        record.role = values["role"]
    if values.get("password"):
        record.password_hash = hash_password(values["password"])
    audit(db, actor, "update", "user", record.id, f"Updated {record.username}")
    db.commit()
    return {"id": record.id, "username": record.username, "role": record.role, "is_active": record.is_active}


@app.delete("/api/v1/users/{user_id}", tags=["users"], dependencies=[Depends(require_csrf)])
def deactivate_user(user_id: int, actor: Annotated[User, Depends(require_permission("users.manage"))], db: Db):
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if record.id == actor.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot deactivate your own account.")
    record.is_active = False
    audit(db, actor, "deactivate", "user", record.id, record.username)
    db.commit()
    return {"ok": True}


@app.get("/api/v1/audit", tags=["audit"])
def list_audit(_user: Annotated[User, Depends(require_permission("users.manage"))], db: Db):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(100)).all()
    return [{
        "action": row.action, "entity": row.entity, "entity_id": row.entity_id,
        "detail": row.detail, "created_at": row.created_at, "username": row.user.username,
    } for row in rows]


@app.get("/api/v1/reports", tags=["reports"])
def reports(user: Annotated[User, Depends(require_permission("reports.read"))], db: Db):
    lots = db.scalars(select(Lot)).all()
    sales = db.scalars(select(Sale)).all()
    stock = {}
    for lot in lots:
        if lot.remaining_m3 > 0:
            entry = stock.setdefault(lot.species, {"species": lot.species, "volume_m3": 0.0, "lots": 0})
            entry["volume_m3"] += lot.remaining_m3
            entry["lots"] += 1
    result = {
        "summary": {
            "stock_m3": sum(lot.remaining_m3 for lot in lots),
            "sold_m3": sum(sale.volume_m3 for sale in sales),
            "lots": len(lots),
        },
        "stock_by_species": sorted(stock.values(), key=lambda entry: entry["species"]),
    }
    if user.role in {"owner", "finance"}:
        result["financial"] = {
            "sales_kes": sum(sale.volume_m3 * sale.price_per_m3 for sale in sales),
            "estimated_cost_kes": sum(sale.volume_m3 * sale.lot.cost_per_m3 for sale in sales),
        }
    return result


def in_date_range(value: str, start_date: date | None, end_date: date | None) -> bool:
    record_date = value[:10]
    return not (start_date and record_date < start_date.isoformat()) and not (
        end_date and record_date > end_date.isoformat()
    )


@app.get("/api/v1/reports/stock", tags=["reports"])
def stock_report(_user: Annotated[User, Depends(require_permission("reports.read"))], db: Db):
    lots = db.scalars(select(Lot).order_by(Lot.location, Lot.species)).all()
    by_location = {}
    by_species = {}
    for lot in lots:
        if lot.remaining_m3 <= 0:
            continue
        location = by_location.setdefault(lot.location, {"location": lot.location, "volume_m3": 0.0, "lots": 0})
        location["volume_m3"] += lot.remaining_m3
        location["lots"] += 1
        species = by_species.setdefault(lot.species, {"species": lot.species, "volume_m3": 0.0, "lots": 0})
        species["volume_m3"] += lot.remaining_m3
        species["lots"] += 1
    return {"by_location": list(by_location.values()), "by_species": list(by_species.values())}


@app.get("/api/v1/reports/purchases-vs-sales", tags=["reports"])
def purchases_vs_sales(
    _user: Annotated[User, Depends(require_permission("reports.read"))],
    db: Db,
    start_date: date | None = Query(default=None, alias="from"),
    end_date: date | None = Query(default=None, alias="to"),
):
    lots = [lot for lot in db.scalars(select(Lot)).all()
            if in_date_range(lot.created_at, start_date, end_date)]
    sales = [sale for sale in db.scalars(select(Sale)).all()
             if in_date_range(sale.created_at, start_date, end_date)]
    return {
        "purchased_m3": sum(lot.volume_m3 for lot in lots),
        "sold_m3": sum(sale.volume_m3 for sale in sales),
        "purchases_kes": sum(lot.volume_m3 * lot.cost_per_m3 for lot in lots),
        "sales_kes": sum(sale.volume_m3 * sale.price_per_m3 for sale in sales),
    }


@app.get("/api/v1/reports/supplier-performance", tags=["reports"])
def supplier_performance(_user: Annotated[User, Depends(require_permission("reports.read"))], db: Db):
    suppliers = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    return [{
        "supplier_id": supplier.id,
        "supplier": supplier.name,
        "lots": len(supplier.lots),
        "purchased_m3": sum(lot.volume_m3 for lot in supplier.lots),
        "purchase_value_kes": sum(lot.volume_m3 * lot.cost_per_m3 for lot in supplier.lots),
    } for supplier in suppliers]


@app.get("/api/v1/reports/margin-by-species", tags=["reports"])
def margin_by_species(
    user: Annotated[User, Depends(require_permission("reports.read"))],
    db: Db,
    start_date: date | None = Query(default=None, alias="from"),
    end_date: date | None = Query(default=None, alias="to"),
):
    if user.role not in {"owner", "finance"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Financial reporting access is required.")
    rows = {}
    sales = [sale for sale in db.scalars(select(Sale)).all()
             if in_date_range(sale.created_at, start_date, end_date)]
    for sale in sales:
        entry = rows.setdefault(sale.lot.species, {
            "species": sale.lot.species, "sold_m3": 0.0, "sales_kes": 0.0, "estimated_cost_kes": 0.0,
        })
        entry["sold_m3"] += sale.volume_m3
        entry["sales_kes"] += sale.volume_m3 * sale.price_per_m3
        entry["estimated_cost_kes"] += sale.volume_m3 * sale.lot.cost_per_m3
    for entry in rows.values():
        entry["estimated_margin_kes"] = entry["sales_kes"] - entry["estimated_cost_kes"]
    return list(rows.values())


@app.get("/api/v1/reports/traceability/{lot_code}", tags=["reports"])
def traceability_report(lot_code: str, user: Annotated[User, Depends(require_permission("lots.read"))], db: Db):
    lot = db.scalar(select(Lot).where(Lot.lot_code == lot_code))
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found.")
    return {"lot": serialize_lot(lot, user.role in {"owner", "finance"}),
            "history": lot_history(lot.id, user, db)}


@app.get("/api/v1/customers", tags=["customers"])
def list_customers(_user: Annotated[User, Depends(current_user)], db: Db):
    return [{"id": item.id, "name": item.name, "phone": item.phone, "created_at": item.created_at}
            for item in db.scalars(select(Customer).order_by(Customer.name)).all()]


@app.post("/api/v1/customers", status_code=status.HTTP_201_CREATED, tags=["customers"],
          dependencies=[Depends(require_csrf)])
def create_customer(data: CustomerCreate, user: Annotated[User, Depends(require_permission("sales.write"))], db: Db):
    customer = Customer(name=data.name.strip(), phone=data.phone.strip(), created_at=timestamp())
    db.add(customer)
    db.flush()
    audit(db, user, "create", "customer", customer.id, customer.name)
    db.commit()
    return {"id": customer.id}


@app.get("/api/v1/locations", tags=["locations"])
def list_locations(_user: Annotated[User, Depends(current_user)], db: Db):
    return [{"id": item.id, "name": item.name, "location_type": item.location_type}
            for item in db.scalars(select(Location).order_by(Location.name)).all()]


@app.post("/api/v1/locations", status_code=status.HTTP_201_CREATED, tags=["locations"],
          dependencies=[Depends(require_csrf)])
def create_location(data: LocationCreate, user: Annotated[User, Depends(require_permission("lots.write"))], db: Db):
    location = Location(name=data.name.strip(), location_type=data.location_type.strip(), created_at=timestamp())
    db.add(location)
    db.flush()
    audit(db, user, "create", "location", location.id, location.name)
    db.commit()
    return {"id": location.id}


@app.post("/api/v1/purchase-orders", status_code=status.HTTP_201_CREATED, tags=["purchasing"],
          dependencies=[Depends(require_csrf)])
def create_purchase_order(data: PurchaseOrderCreate,
                          user: Annotated[User, Depends(require_permission("lots.write"))], db: Db):
    if not db.get(Supplier, data.supplier_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found.")
    order = PurchaseOrder(order_code=data.order_code.strip(), supplier_id=data.supplier_id,
                          status="ordered", notes=data.notes.strip(), created_by=user.id, created_at=timestamp())
    db.add(order)
    db.flush()
    audit(db, user, "create", "purchase_order", order.id, order.order_code)
    db.commit()
    return {"id": order.id}


@app.post("/api/v1/purchase-orders/{order_id}/receive", status_code=status.HTTP_201_CREATED,
          tags=["purchasing"], dependencies=[Depends(require_csrf)])
def receive_purchase_order(order_id: int, data: PurchaseOrderReceive,
                           user: Annotated[User, Depends(require_permission("lots.write"))], db: Db):
    order = db.get(PurchaseOrder, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase order not found.")
    lot = Lot(
        lot_code=data.lot_code.strip(), supplier_id=order.supplier_id, species=data.species.strip(),
        grade=data.grade.strip(), volume_m3=data.volume_m3, remaining_m3=data.volume_m3,
        cost_per_m3=data.cost_per_m3, location=data.location.strip(),
        origin_document=data.origin_document.strip(), dimensions=data.dimensions,
        pieces=data.pieces, purchase_order_id=order.id, status="in_stock", created_at=timestamp(),
    )
    db.add(lot)
    db.flush()
    db.add(Movement(lot_id=lot.id, kind="received", detail=f"Received at {lot.location}",
                    volume_m3=lot.volume_m3, user_id=user.id, created_at=timestamp()))
    order.status = "received"
    audit(db, user, "receive", "lot", lot.id, f"{lot.lot_code}: {lot.volume_m3} m3")
    db.commit()
    return {"id": lot.id, "purchase_order_id": order.id}


@app.get("/api/v1/purchase-orders", tags=["purchasing"])
def list_purchase_orders(_user: Annotated[User, Depends(require_permission("lots.read"))], db: Db):
    return [{
        "id": item.id, "order_code": item.order_code, "supplier_id": item.supplier_id,
        "supplier_name": item.supplier.name, "status": item.status, "notes": item.notes,
        "created_at": item.created_at,
    } for item in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id.desc())).all()]


@app.get("/api/v1/sales-orders", tags=["sales"])
def list_sales_orders(_user: Annotated[User, Depends(require_permission("sales.read"))], db: Db):
    return [{
        "id": item.id, "order_code": item.order_code, "customer_id": item.customer_id,
        "customer": item.customer_record.name, "status": item.status, "notes": item.notes,
        "created_at": item.created_at,
    } for item in db.scalars(select(SalesOrder).order_by(SalesOrder.id.desc())).all()]


@app.post("/api/v1/sales-orders", status_code=status.HTTP_201_CREATED, tags=["sales"],
          dependencies=[Depends(require_csrf)])
def create_sales_order(data: SalesOrderCreate,
                       user: Annotated[User, Depends(require_permission("sales.write"))], db: Db):
    if not db.get(Customer, data.customer_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found.")
    order = SalesOrder(order_code=data.order_code.strip(), customer_id=data.customer_id,
                       status="draft", notes=data.notes.strip(), created_by=user.id, created_at=timestamp())
    db.add(order)
    db.flush()
    audit(db, user, "create", "sales_order", order.id, order.order_code)
    db.commit()
    return {"id": order.id}
