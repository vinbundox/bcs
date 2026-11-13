from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Role = Literal["owner", "sourcing", "yard", "sales", "finance", "viewer"]
LotCategory = Literal["timber", "poles", "posts", "other"]


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class SetupCredentials(Credentials):
    password: str = Field(min_length=12, max_length=256)


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=60)


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(default="", max_length=60)


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location_type: str = Field(default="yard", min_length=1, max_length=60)


class PurchaseOrderCreate(BaseModel):
    order_code: str = Field(min_length=1, max_length=100)
    supplier_id: int = Field(gt=0)
    notes: str = Field(default="", max_length=2000)


class PurchaseOrderReceive(BaseModel):
    lot_code: str = Field(min_length=1, max_length=100)
    category: LotCategory = "timber"
    species: str = Field(min_length=1, max_length=100)
    grade: str = Field(default="", max_length=100)
    volume_m3: float = Field(gt=0, allow_inf_nan=False)
    cost_per_m3: float = Field(ge=0, allow_inf_nan=False)
    location: str = Field(min_length=1, max_length=200)
    origin_document: str = Field(default="", max_length=200)
    dimensions: str | None = Field(default=None, max_length=100)
    pieces: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_whole_piece_quantity(self):
        if self.category in {"poles", "posts"} and not self.volume_m3.is_integer():
            raise ValueError("Pole and post quantities must be whole pieces.")
        return self


class SalesOrderCreate(BaseModel):
    order_code: str = Field(min_length=1, max_length=100)
    customer_id: int = Field(gt=0)
    notes: str = Field(default="", max_length=2000)


class UserPatch(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=80)
    password: str | None = Field(default=None, min_length=12, max_length=256)
    role: Role | None = None


class LotCreate(BaseModel):
    lot_code: str = Field(min_length=1, max_length=100)
    supplier_id: int = Field(gt=0)
    category: LotCategory = "timber"
    species: str = Field(min_length=1, max_length=100)
    grade: str = Field(default="", max_length=100)
    volume_m3: float = Field(gt=0, allow_inf_nan=False)
    cost_per_m3: float = Field(ge=0, allow_inf_nan=False)
    location: str = Field(min_length=1, max_length=200)
    origin_document: str = Field(default="", max_length=200)
    dimensions: str | None = Field(default=None, max_length=100)
    pieces: int | None = Field(default=None, ge=1)
    purchase_order_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_whole_piece_quantity(self):
        if self.category in {"poles", "posts"} and not self.volume_m3.is_integer():
            raise ValueError("Pole and post quantities must be whole pieces.")
        return self


class LotPurchaseEdit(BaseModel):
    lot_code: str = Field(min_length=1, max_length=100)
    supplier_id: int = Field(gt=0)
    category: LotCategory = "timber"
    species: str = Field(min_length=1, max_length=100)
    grade: str = Field(default="", max_length=100)
    cost_per_m3: float = Field(ge=0, allow_inf_nan=False)
    location: str = Field(min_length=1, max_length=200)
    origin_document: str = Field(default="", max_length=200)
    dimensions: str | None = Field(default=None, max_length=100)
    pieces: int | None = Field(default=None, ge=1)


class MoveRequest(BaseModel):
    location: str = Field(min_length=1, max_length=200)


class MovementCreate(BaseModel):
    kind: Literal["graded", "move", "reserve", "unreserve", "delivered"]
    location: str | None = Field(default=None, max_length=200)
    detail: str = Field(default="", max_length=500)


class SaleCreate(BaseModel):
    lot_id: int = Field(gt=0)
    customer: str = Field(min_length=1, max_length=200)
    volume_m3: float = Field(gt=0, allow_inf_nan=False)
    price_per_m3: float = Field(ge=0, allow_inf_nan=False)


class SaleEdit(BaseModel):
    customer: str = Field(min_length=1, max_length=200)
    volume_m3: float = Field(gt=0, allow_inf_nan=False)
    price_per_m3: float = Field(ge=0, allow_inf_nan=False)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=256)
    role: Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    created_at: str
    is_active: bool
