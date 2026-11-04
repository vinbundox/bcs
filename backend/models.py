from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(
        "role IN ('owner','sourcing','yard','sales','finance','viewer')", name="ck_users_role"
    ),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    lots: Mapped[list["Lot"]] = relationship(back_populates="supplier")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    location_type: Mapped[str] = mapped_column(String, nullable=False, default="yard")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="ordered")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    supplier: Mapped[Supplier] = relationship()


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (
        CheckConstraint("volume_m3 > 0", name="ck_lots_volume_positive"),
        CheckConstraint("remaining_m3 >= 0", name="ck_lots_remaining_nonnegative"),
        CheckConstraint("cost_per_m3 >= 0", name="ck_lots_cost_nonnegative"),
        Index("ix_lots_supplier_id", "supplier_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    species: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str] = mapped_column(String, nullable=False, default="")
    volume_m3: Mapped[float] = mapped_column(nullable=False)
    remaining_m3: Mapped[float] = mapped_column(nullable=False)
    cost_per_m3: Mapped[float] = mapped_column(nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    origin_document: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    dimensions: Mapped[str | None] = mapped_column(String, nullable=True)
    pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="in_stock")
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    supplier: Mapped[Supplier] = relationship(back_populates="lots")
    movements: Mapped[list["Movement"]] = relationship(back_populates="lot", order_by="Movement.id")


class Movement(Base):
    __tablename__ = "movements"
    __table_args__ = (Index("ix_movements_lot_id", "lot_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    volume_m3: Mapped[float] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    lot: Mapped[Lot] = relationship(back_populates="movements")
    user: Mapped[User] = relationship()


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("volume_m3 > 0", name="ck_sales_volume_positive"),
        CheckConstraint("price_per_m3 >= 0", name="ck_sales_price_nonnegative"),
        Index("ix_sales_lot_id", "lot_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), nullable=False)
    customer: Mapped[str] = mapped_column(String, nullable=False)
    volume_m3: Mapped[float] = mapped_column(nullable=False)
    price_per_m3: Mapped[float] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    lot: Mapped[Lot] = relationship()
    user: Mapped[User] = relationship()


class SalesOrder(Base):
    __tablename__ = "sales_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    customer_record: Mapped[Customer] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    entity: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=timestamp)
    user: Mapped[User] = relationship()
