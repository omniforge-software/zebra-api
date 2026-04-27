from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Zebra API"
    database_url: str = "sqlite:///./data/zebra.db"
    secret_key: str = Field(..., min_length=32)

    admin_username: str = "admin"
    admin_password_hash: str = Field(..., min_length=20)
    admin_jwt_minutes: int = 480
    cookie_secure: bool = False

    scan_subnets: str = ""
    scan_on_startup: bool = True
    scan_ports: str = "9100,515,631,80"
    scan_connect_timeout_seconds: float = 0.5
    scanner_workers: int = 100

    printer_port: int = 9100
    printer_connect_timeout_seconds: float = 3.0
    printer_read_timeout_seconds: float = 3.0
    printer_write_timeout_seconds: float = 5.0
    printer_status_timeout_seconds: float = 2.0
    printer_max_concurrent_per_device: int = 1
    print_retry_count: int = 1
    max_print_quantity: int = 100
    max_zpl_bytes: int = 262_144

    status_stale_after_seconds: int = 300
    job_retention_days: int = 31
    cleanup_interval_minutes: int = 60

    @property
    def scan_port_list(self) -> list[int]:
        return [int(port.strip()) for port in self.scan_ports.split(",") if port.strip()]

    @property
    def scan_subnet_list(self) -> list[str]:
        return [subnet.strip() for subnet in self.scan_subnets.split(",") if subnet.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
