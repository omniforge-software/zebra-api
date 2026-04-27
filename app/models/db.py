import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    return str(uuid.uuid4())


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    key_hash: Mapped[str] = mapped_column(String(255), index=True)
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    jobs: Mapped[list["PrintJob"]] = relationship(back_populates="api_key")


class Printer(Base):
    __tablename__ = "printers"
    __table_args__ = (UniqueConstraint("ip", name="uq_printer_ip"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    ip: Mapped[str] = mapped_column(String(45), unique=True, index=True)
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    friendly_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(255), nullable=True)
    print_width: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ports_open: Mapped[list[int]] = mapped_column(JSON, default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)

    jobs: Mapped[list["PrintJob"]] = relationship(back_populates="printer")

    @property
    def display_name(self) -> str:
        return self.alias or self.friendly_name or self.product_name or self.ip


class LabelTemplate(Base):
    __tablename__ = "label_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    zpl_body: Mapped[str] = mapped_column(Text)
    variables: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    jobs: Mapped[list["PrintJob"]] = relationship(back_populates="template")


class PrintJob(Base):
    __tablename__ = "print_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    api_key_id: Mapped[str | None] = mapped_column(ForeignKey("api_keys.id"), nullable=True)
    printer_id: Mapped[str] = mapped_column(ForeignKey("printers.id"))
    template_id: Mapped[str] = mapped_column(ForeignKey("label_templates.id"))
    variables: Mapped[dict] = mapped_column(JSON, default=dict)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    api_key: Mapped[ApiKey | None] = relationship(back_populates="jobs")
    printer: Mapped[Printer] = relationship(back_populates="jobs")
    template: Mapped[LabelTemplate] = relationship(back_populates="jobs")
