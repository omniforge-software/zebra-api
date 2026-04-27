from pydantic import BaseModel, ConfigDict, Field


class PrinterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ip: str
    alias: str | None = None
    friendly_name: str | None = None
    product_name: str | None = None
    firmware: str | None = None
    dpi: int | None = None
    print_width: str | None = None
    label_length: str | None = None
    media_type: str | None = None
    media_out: bool | None = None
    odometer: str | None = None
    ports_open: list[int]
    is_online: bool


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    variables: list[str]


class PrintRequest(BaseModel):
    printer_id: str
    template_id: str
    variables: dict[str, str] = Field(default_factory=dict)
    quantity: int = Field(default=1, ge=1)


class PrintJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    printer_id: str
    template_id: str
    quantity: int
    status: str
    error_message: str | None = None
