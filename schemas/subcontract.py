from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Text20 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Text50 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Text100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PositiveQty = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def iso_date(value):
    if value is None:
        return value
    if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
        raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return value


class SubcontractOrderItemInput(Input):
    previous_part_no: Text50
    processing_type_code: Text50
    order_part_no: str | None = Field(default=None, max_length=80)
    order_qty: PositiveQty
    delivery_date: str | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator("delivery_date")
    @classmethod
    def validate_delivery_date(cls, value):
        return iso_date(value)


class SubcontractOrderInput(Input):
    order_date: str
    partner_id: int = Field(gt=0)
    partner_name: Text100
    processing_type_code: Text50
    delivery_due_date: str | None = None
    external_storage_location: Text20
    manager_name: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=1000)
    items: list[SubcontractOrderItemInput] = Field(min_length=1, max_length=500)

    @field_validator("order_date", "delivery_due_date")
    @classmethod
    def validate_dates(cls, value):
        return iso_date(value)


class LotAllocationInput(Input):
    lot_nos: list[Text100] = Field(min_length=1, max_length=1000)
