import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from jose import JWTError, jwt
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.staticfiles import StaticFiles

from backend.database import get_db
from backend.models import (
    PIECE_CATEGORIES, AuditLog, Customer, Lot, Location, Movement, PurchaseOrder, Sale,
    SalesOrder, Supplier, User,
)
from backend.schemas import (
    Credentials, CustomerCreate, LocationCreate, LotCreate, MoveRequest, PurchaseOrderCreate,
    LotPurchaseEdit, MovementCreate, PurchaseOrderReceive, SaleCreate, SaleEdit, SalesOrderCreate, SetupCredentials,
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
FRONTEND_DIR = Path(os.environ.get(
    "TIMBER_FRONTEND_DIR", str(Path(__file__).resolve().parents[1] / "frontend" / "dist")
))
LOT_CATEGORIES = ("timber", "poles", "posts", "other")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def frontend_root():
    index = FRONTEND_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {
        "name": "TimberTrack API",
        "message": "The API is running. Build the React frontend to serve the app here.",
        "docs": "/api/v1/docs",
        "health": "/healthz",
    }


def unit_for_category(category: str) -> str:
    return "pcs" if category in PIECE_CATEGORIES else "m3"


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
    unit = "pcs" if lot.category in PIECE_CATEGORIES else "m3"
    result = {
        "id": lot.id, "lot_code": lot.lot_code, "supplier_id": lot.supplier_id,
        "supplier_name": lot.supplier.name, "category": lot.category, "unit": unit,
        "quantity": float(lot.volume_m3), "remaining_quantity": remaining,
        "species": lot.species, "grade": lot.grade,
        "volume_m3": float(lot.volume_m3), "remaining_m3": remaining, "location": lot.location,
        "origin_document": lot.origin_document, "created_at": lot.created_at, "status": status_label,
        "dimensions": lot.dimensions, "pieces": lot.pieces, "lot_status": lot.status,
        "purchase_order_id": lot.purchase_order_id,
    }
    if include_cost:
        result["cost_per_m3"] = float(lot.cost_per_m3)
        result["buying_rate"] = float(lot.cost_per_m3)
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
    audit(db, user, "receive", "lot", lot.id, f"{lot.lot_code}: {lot.volume_m3} {unit_for_category(lot.category)}")
    db.commit()
    return {"id": lot.id}


@app.put("/api/v1/lots/{lot_id}/purchase", tags=["lots"],
         dependencies=[Depends(require_csrf)])
def edit_lot_purchase(
    lot_id: int,
    data: LotPurchaseEdit,
    user: Annotated[User, Depends(current_user)],
    db: Db,
):
    if user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can edit purchase details.")
    lot = db.get(Lot, lot_id)
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found.")
    supplier = db.get(Supplier, data.supplier_id)
    if not supplier:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found.")

    if data.category != lot.category:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A lot's category cannot be changed after receipt because its stock unit is category-specific.",
        )
    fields = (
        "lot_code", "supplier_id", "category", "species", "grade", "cost_per_m3",
        "location", "origin_document", "dimensions", "pieces",
    )
    before = {field: getattr(lot, field) for field in fields}
    after = data.model_dump()
    after["lot_code"] = after["lot_code"].strip()
    after["species"] = after["species"].strip()
    after["grade"] = after["grade"].strip()
    after["location"] = after["location"].strip()
    after["origin_document"] = after["origin_document"].strip()
    after["dimensions"] = after["dimensions"].strip() if after["dimensions"] else None
    if not after["lot_code"] or not after["species"] or not after["location"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Lot code, species, and location are required.")
    for field in fields:
        setattr(lot, field, after[field])
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That lot code is already in use.")
    detail = json.dumps({"before": before, "after": after}, ensure_ascii=False, default=str)
    db.add(Movement(
        lot_id=lot.id,
        kind="purchase_edited",
        detail=f"Purchase details updated: {detail}",
        volume_m3=lot.remaining_m3,
        user_id=user.id,
        created_at=timestamp(),
    ))
    audit(db, user, "edit_purchase", "lot", lot.id, detail)
    db.commit()
    return {"ok": True}


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
        "id": sale.id, "customer": sale.customer, "category": sale.lot.category,
        "unit": unit_for_category(sale.lot.category),
        "quantity": float(sale.volume_m3), "unit_price": float(sale.price_per_m3),
        "volume_m3": sale.volume_m3,
        "price_per_m3": sale.price_per_m3, "created_at": sale.created_at,
        "lot_code": sale.lot.lot_code, "species": sale.lot.species, "recorded_by": sale.user.username,
    } for sale in sales]


@app.post("/api/v1/sales", status_code=status.HTTP_201_CREATED, tags=["sales"],
          dependencies=[Depends(require_csrf)])
def create_sale(data: SaleCreate, user: Annotated[User, Depends(require_permission("sales.write"))], db: Db):
    lot = db.get(Lot, data.lot_id)
    if not lot:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lot not found.")
    if lot.category in PIECE_CATEGORIES and not float(data.volume_m3).is_integer():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pole and post sale quantities must be whole pieces.")
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


@app.put("/api/v1/sales/{sale_id}", tags=["sales"],
         dependencies=[Depends(require_csrf)])
def edit_sale(
    sale_id: int,
    data: SaleEdit,
    user: Annotated[User, Depends(current_user)],
    db: Db,
):
    if user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can edit sales and invoices.")
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found.")
    customer = data.customer.strip()
    if not customer:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Customer name is required.")
    lot = db.get(Lot, sale.lot_id)
    old_volume = float(sale.volume_m3)
    new_volume = float(data.volume_m3)
    if lot.category in PIECE_CATEGORIES and not new_volume.is_integer():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pole and post sale quantities must be whole pieces.")
    volume_delta = new_volume - old_volume
    if volume_delta > 0 and lot.status not in {"in_stock", "graded"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Sale volume cannot be increased while its lot is reserved or delivered.",
        )
    if volume_delta:
        stock_update = update(Lot).where(Lot.id == lot.id)
        if volume_delta > 0:
            stock_update = stock_update.where(Lot.remaining_m3 >= volume_delta)
        stock_result = db.execute(
            stock_update.values(
                remaining_m3=Lot.remaining_m3 - volume_delta,
                status="in_stock" if volume_delta < 0 and lot.status == "delivered" else Lot.status,
            )
        )
        if not stock_result.rowcount:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Sale volume exceeds available stock.")

    before = {
        "customer": sale.customer,
        "volume_m3": old_volume,
        "price_per_m3": float(sale.price_per_m3),
    }
    after = {
        "customer": customer,
        "volume_m3": new_volume,
        "price_per_m3": float(data.price_per_m3),
    }
    sale.customer = after["customer"]
    sale.volume_m3 = new_volume
    sale.price_per_m3 = after["price_per_m3"]
    detail = json.dumps({"before": before, "after": after}, ensure_ascii=False)
    db.add(Movement(
        lot_id=lot.id,
        kind="sale_edited",
        detail=f"Sale {sale.id} updated: {detail}",
        volume_m3=abs(volume_delta),
        user_id=user.id,
        created_at=timestamp(),
    ))
    audit(db, user, "edit_sale", "sale", sale.id, detail)
    db.commit()
    return {"ok": True}


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
    lot_financials = {}
    species_financials = {}
    category_totals = {
        category: {
            "category": category,
            "unit": unit_for_category(category),
            "available_quantity": 0.0,
            "sold_quantity": 0.0,
            "lots": 0,
        }
        for category in LOT_CATEGORIES
    }
    for lot in lots:
        category = lot.category if lot.category in LOT_CATEGORIES else "other"
        unit = unit_for_category(category)
        amount = float(lot.volume_m3)
        remaining = float(lot.remaining_m3)
        lot_entry = lot_financials.setdefault(lot.id, {
            "lot_code": lot.lot_code,
            "category": category,
            "unit": unit,
            "species": lot.species,
            "available_quantity": remaining,
            "available_m3": remaining if unit == "m3" else 0.0,
            "purchase_rate": float(lot.cost_per_m3),
            "purchase_rate_kes_per_m3": float(lot.cost_per_m3) if unit == "m3" else 0.0,
            "inventory_value_kes": float(lot.remaining_m3) * float(lot.cost_per_m3),
            "sold_quantity": 0.0,
            "sold_m3": 0.0,
            "sales_revenue_kes": 0.0,
            "cost_of_goods_sold_kes": 0.0,
        })
        species_key = (category, lot.species)
        species_entry = species_financials.setdefault(species_key, {
            "category": category,
            "unit": unit,
            "species": lot.species,
            "available_quantity": 0.0,
            "available_m3": 0.0,
            "inventory_value_kes": 0.0,
            "sold_quantity": 0.0,
            "sold_m3": 0.0,
            "sales_revenue_kes": 0.0,
            "cost_of_goods_sold_kes": 0.0,
            "purchase_quantity": 0.0,
            "purchase_volume_m3": 0.0,
            "purchase_value_kes": 0.0,
        })
        category_totals[category]["available_quantity"] += remaining
        if remaining > 0:
            category_totals[category]["lots"] += 1
        species_entry["available_quantity"] += remaining
        species_entry["available_m3"] += remaining if unit == "m3" else 0.0
        species_entry["inventory_value_kes"] += float(lot.remaining_m3) * float(lot.cost_per_m3)
        species_entry["purchase_quantity"] += amount
        species_entry["purchase_volume_m3"] += amount if unit == "m3" else 0.0
        species_entry["purchase_value_kes"] += float(lot.volume_m3) * float(lot.cost_per_m3)
        if lot.remaining_m3 > 0:
            entry = stock.setdefault(species_key, {
                "category": category, "unit": unit, "species": lot.species,
                "quantity": 0.0, "volume_m3": 0.0, "lots": 0,
            })
            entry["quantity"] += remaining
            if unit == "m3":
                entry["volume_m3"] += remaining
            entry["lots"] += 1
    for sale in sales:
        category = sale.lot.category if sale.lot.category in LOT_CATEGORIES else "other"
        unit = unit_for_category(category)
        quantity = float(sale.volume_m3)
        revenue = float(sale.volume_m3) * float(sale.price_per_m3)
        cost = float(sale.volume_m3) * float(sale.lot.cost_per_m3)
        lot_entry = lot_financials[sale.lot_id]
        lot_entry["sold_quantity"] += quantity
        lot_entry["sold_m3"] += quantity if unit == "m3" else 0.0
        lot_entry["sales_revenue_kes"] += revenue
        lot_entry["cost_of_goods_sold_kes"] += cost
        species_entry = species_financials[(category, sale.lot.species)]
        species_entry["sold_quantity"] += quantity
        species_entry["sold_m3"] += quantity if unit == "m3" else 0.0
        species_entry["sales_revenue_kes"] += revenue
        species_entry["cost_of_goods_sold_kes"] += cost
        category_totals[category]["sold_quantity"] += quantity
    for entry in species_financials.values():
        entry["average_purchase_rate"] = (
            entry["purchase_value_kes"] / entry["purchase_quantity"]
            if entry["purchase_quantity"] else 0.0
        )
        entry["average_purchase_rate_kes_per_m3"] = (
            entry["average_purchase_rate"] if entry["unit"] == "m3" else 0.0
        )
        entry["gross_profit_kes"] = entry["sales_revenue_kes"] - entry["cost_of_goods_sold_kes"]
    for entry in lot_financials.values():
        entry["gross_profit_kes"] = entry["sales_revenue_kes"] - entry["cost_of_goods_sold_kes"]
    result = {
        "summary": {
            "stock_m3": sum(lot.remaining_m3 for lot in lots if lot.category not in PIECE_CATEGORIES),
            "sold_m3": sum(
                sale.volume_m3 for sale in sales if sale.lot.category not in PIECE_CATEGORIES
            ),
            "stock_by_category": list(category_totals.values()),
            "lots": len(lots),
        },
        "stock_by_species": sorted(stock.values(), key=lambda entry: (entry["category"], entry["species"])),
    }
    if user.role in {"owner", "finance"}:
        sales_revenue = sum(float(sale.volume_m3) * float(sale.price_per_m3) for sale in sales)
        cost_of_goods_sold = sum(
            float(sale.volume_m3) * float(sale.lot.cost_per_m3) for sale in sales
        )
        result["financial"] = {
            "sales_kes": sales_revenue,
            "estimated_cost_kes": cost_of_goods_sold,
            "inventory_value_kes": sum(
                float(lot.remaining_m3) * float(lot.cost_per_m3) for lot in lots
            ),
            "profit_and_loss": {
                "sales_revenue_kes": sales_revenue,
                "cost_of_goods_sold_kes": cost_of_goods_sold,
                "gross_profit_kes": sales_revenue - cost_of_goods_sold,
            },
            "by_species": sorted(species_financials.values(), key=lambda entry: (entry["category"], entry["species"])),
            "by_lot": sorted(lot_financials.values(), key=lambda entry: entry["lot_code"]),
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
    by_category = {}
    for lot in lots:
        if lot.remaining_m3 <= 0:
            continue
        category = lot.category if lot.category in LOT_CATEGORIES else "other"
        unit = unit_for_category(category)
        location_key = (lot.location, category)
        location = by_location.setdefault(location_key, {
            "location": lot.location, "category": category, "unit": unit, "quantity": 0.0, "volume_m3": 0.0, "lots": 0,
        })
        location["quantity"] += lot.remaining_m3
        if unit == "m3":
            location["volume_m3"] += lot.remaining_m3
        location["lots"] += 1
        species_key = (lot.species, category)
        species = by_species.setdefault(species_key, {
            "species": lot.species, "category": category, "unit": unit, "quantity": 0.0, "volume_m3": 0.0, "lots": 0,
        })
        species["quantity"] += lot.remaining_m3
        if unit == "m3":
            species["volume_m3"] += lot.remaining_m3
        species["lots"] += 1
        category_row = by_category.setdefault(category, {
            "category": category, "unit": unit, "quantity": 0.0, "lots": 0,
        })
        category_row["quantity"] += lot.remaining_m3
        category_row["lots"] += 1
    return {
        "by_location": list(by_location.values()),
        "by_species": list(by_species.values()),
        "by_category": list(by_category.values()),
    }


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
    by_category = {}
    for lot in lots:
        category = lot.category if lot.category in LOT_CATEGORIES else "other"
        row = by_category.setdefault(category, {
            "category": category, "unit": unit_for_category(category),
            "purchased_quantity": 0.0, "purchase_value_kes": 0.0,
        })
        row["purchased_quantity"] += lot.volume_m3
        row["purchase_value_kes"] += lot.volume_m3 * lot.cost_per_m3
    for sale in sales:
        category = sale.lot.category if sale.lot.category in LOT_CATEGORIES else "other"
        row = by_category.setdefault(category, {
            "category": category, "unit": unit_for_category(category),
            "purchased_quantity": 0.0, "purchase_value_kes": 0.0,
        })
        row["sold_quantity"] = row.get("sold_quantity", 0.0) + sale.volume_m3
        row["sales_value_kes"] = row.get("sales_value_kes", 0.0) + sale.volume_m3 * sale.price_per_m3
    return {
        "purchased_m3": sum(lot.volume_m3 for lot in lots if lot.category not in PIECE_CATEGORIES),
        "sold_m3": sum(sale.volume_m3 for sale in sales if sale.lot.category not in PIECE_CATEGORIES),
        "purchases_kes": sum(lot.volume_m3 * lot.cost_per_m3 for lot in lots),
        "sales_kes": sum(sale.volume_m3 * sale.price_per_m3 for sale in sales),
        "by_category": list(by_category.values()),
    }


@app.get("/api/v1/reports/supplier-performance", tags=["reports"])
def supplier_performance(_user: Annotated[User, Depends(require_permission("reports.read"))], db: Db):
    suppliers = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    return [{
        "supplier_id": supplier.id,
        "supplier": supplier.name,
        "lots": len(supplier.lots),
        "purchased_m3": sum(lot.volume_m3 for lot in supplier.lots if lot.category not in PIECE_CATEGORIES),
        "purchased_by_category": [
            {
                "category": category,
                "unit": unit_for_category(category),
                "quantity": sum(lot.volume_m3 for lot in supplier.lots if lot.category == category),
            }
            for category in LOT_CATEGORIES
            if any(lot.category == category for lot in supplier.lots)
        ],
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
        category = sale.lot.category if sale.lot.category in LOT_CATEGORIES else "other"
        key = (category, sale.lot.species)
        entry = rows.setdefault(key, {
            "category": category,
            "unit": unit_for_category(category),
            "species": sale.lot.species,
            "sold_quantity": 0.0,
            "sold_m3": 0.0,
            "sales_kes": 0.0,
            "estimated_cost_kes": 0.0,
        })
        entry["sold_quantity"] += sale.volume_m3
        if category not in PIECE_CATEGORIES:
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
        category=data.category, grade=data.grade.strip(), volume_m3=data.volume_m3, remaining_m3=data.volume_m3,
        cost_per_m3=data.cost_per_m3, location=data.location.strip(),
        origin_document=data.origin_document.strip(), dimensions=data.dimensions,
        pieces=data.pieces, purchase_order_id=order.id, status="in_stock", created_at=timestamp(),
    )
    db.add(lot)
    db.flush()
    db.add(Movement(lot_id=lot.id, kind="received", detail=f"Received at {lot.location}",
                    volume_m3=lot.volume_m3, user_id=user.id, created_at=timestamp()))
    order.status = "received"
    audit(db, user, "receive", "lot", lot.id, f"{lot.lot_code}: {lot.volume_m3} {unit_for_category(lot.category)}")
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


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend-assets")
