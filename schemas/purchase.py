from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Text20 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Text50 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Text100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PositiveQty = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Money = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def iso_date(value):
    if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
        raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return value


class OrderItemCreate(Input):
    part_no: Text50
    order_qty: PositiveQty
    delivery_date: str | None = None
    warehouse_code: Text20
    storage_location: Text20
    note: str | None = Field(default=None, max_length=500)
    unit_price: Money = 0.0
    supply_price: Money = 0.0
    vat_price: Money = 0.0

    @field_validator("delivery_date")
    @classmethod
    def validate_delivery_date(cls, value):
        return iso_date(value) if value is not None else value


class Header(Input):
    partner_id: int | None = Field(default=None, gt=0)
    partner_name: Text100
    created_by: Text50 | None = None


class OrderCreate(Header):
    partner_id: int = Field(gt=0)
    order_date: str
    delivery_due_date: str | None = None
    manager_name: str | None = Field(default=None, max_length=50)
    note: str | None = None
    items: list[OrderItemCreate] = Field(min_length=1, max_length=1000)

    @field_validator("order_date", "delivery_due_date")
    @classmethod
    def validate_dates(cls, value):
        return iso_date(value) if value is not None else value


class InboundItemCreate(Input):
    inbound_item_id: int | None = Field(default=None, gt=0)
    po_item_id: int | None = Field(default=None, gt=0)
    part_no: Text50
    inbound_qty: PositiveQty
    supplier_lot_no: Text100
    internal_lot_no: Text100 | None = None
    note: str | None = Field(default=None, max_length=500)
    warehouse_code: Text20
    storage_location: Text20
    unit_price: Money = 0.0


class InboundCreate(Header):
    inbound_date: str
    invoice_no: Text50 | None = None
    note: str | None = Field(default=None, max_length=1000)
    items: list[InboundItemCreate] = Field(min_length=1, max_length=1000)

    @field_validator("inbound_date")
    @classmethod
    def validate_date(cls, value):
        return iso_date(value)


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrderItemOut(Output):
    id: int
    po_id: int
    part_no: str
    order_qty: float
    unit: str
    received_qty: float
    delivery_date: str | None
    warehouse_code: str | None
    storage_location: str | None
    note: str | None
    unit_price: float
    supply_price: float
    vat_price: float
    status: str


class OrderOut(Output):
    id: int
    po_no: str
    order_date: str
    delivery_due_date: str | None
    partner_id: int | None
    partner_name: str
    status: str
    manager_name: str | None
    note: str | None
    created_by: str | None
    created_at: datetime
    items: list[OrderItemOut]


class InboundItemOut(Output):
    id: int
    inbound_id: int
    po_item_id: int | None
    part_no: str
    inbound_qty: float
    supplier_lot_no: str
    internal_lot_no: str | None
    note: str | None
    warehouse_code: str
    storage_location: str
    unit_price: float


class InboundOut(Output):
    id: int
    inbound_no: str
    inbound_date: str
    partner_id: int | None
    partner_name: str
    invoice_no: str | None
    status: str
    note: str | None
    created_by: str | None
    created_at: datetime
    items: list[InboundItemOut]
