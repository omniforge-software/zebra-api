import re

from app.config import get_settings


PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def extract_variables(zpl: str) -> list[str]:
    return sorted(set(PLACEHOLDER_RE.findall(zpl)))


def render_zpl(template: str, declared_variables: list[str], values: dict[str, str], quantity: int) -> bytes:
    settings = get_settings()
    missing = [name for name in declared_variables if name not in values]
    if missing:
        raise ValueError(f"Missing template variables: {', '.join(missing)}")

    extra = [name for name in values if name not in declared_variables]
    if extra:
        raise ValueError(f"Unknown template variables: {', '.join(extra)}")

    rendered = PLACEHOLDER_RE.sub(lambda match: str(values[match.group(1)]), template)
    rendered = inject_quantity(rendered, quantity)
    validate_zpl(rendered)
    payload = rendered.encode("utf-8")
    if len(payload) > settings.max_zpl_bytes:
        raise ValueError(f"Rendered ZPL is too large ({len(payload)} bytes)")
    return payload


def inject_quantity(zpl: str, quantity: int) -> str:
    if quantity < 1:
        raise ValueError("Quantity must be at least 1")

    settings = get_settings()
    if quantity > settings.max_print_quantity:
        raise ValueError(f"Quantity cannot exceed {settings.max_print_quantity}")

    zpl = zpl.strip()
    if "^PQ" in zpl:
        return re.sub(r"\^PQ\d+", f"^PQ{quantity}", zpl, count=1)
    if "^XZ" not in zpl:
        raise ValueError("ZPL must end with ^XZ")
    return zpl.replace("^XZ", f"^PQ{quantity}\n^XZ", 1)


def validate_zpl(zpl: str) -> None:
    stripped = zpl.strip()
    if not stripped.startswith("^XA"):
        raise ValueError("ZPL must start with ^XA")
    if not stripped.endswith("^XZ"):
        raise ValueError("ZPL must end with ^XZ")
