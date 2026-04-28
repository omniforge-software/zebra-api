import asyncio
import ipaddress
import re
import socket
import subprocess
from datetime import datetime, timezone

from app.config import get_settings
from app.services.printer_io import query_sgd, query_zpl


def _parse_dpi(raw: str) -> int | None:
    """Parse DPI from Zebra's device.printhead.resolution response.

    Zebra returns values like '8 dpmm', '12 dpmm', or plain integers.
    8 dpmm = 203 dpi, 12 dpmm = 300 dpi, 24 dpmm = 600 dpi.
    """
    raw = raw.strip().strip('"').lower()
    dpmm_match = re.search(r"(\d+)\s*dpmm", raw)
    if dpmm_match:
        return round(int(dpmm_match.group(1)) * 25.4)
    # Some printers return plain dpi number
    dpi_match = re.search(r"(\d+)", raw)
    if dpi_match:
        val = int(dpi_match.group(1))
        # dpmm values (6-24) need converting; dpi values are >100
        if val <= 24:
            return round(val * 25.4)
        return val
    return None


def _dots_to_mm(dots_raw: str, dpi: int) -> str:
    """Convert a raw dot-count string to millimeters, e.g. '1205' → '150.6 mm'."""
    try:
        dots = int(dots_raw.strip().strip('"'))
        return f"{(dots / dpi) * 25.4:.1f} mm"
    except (ValueError, ZeroDivisionError):
        return dots_raw


def _resolve_dpi(dpi_raw: str | None, dpmm_raw: str | None, current_dpi: int | None = None) -> int | None:
    """Resolve DPI from common Zebra SGD responses with sensible fallback order."""
    if dpi_raw:
        parsed = _parse_dpi(dpi_raw)
        if parsed:
            return parsed
    if dpmm_raw:
        parsed = _parse_dpi(dpmm_raw)
        if parsed:
            return parsed
    return current_dpi


def get_local_subnets() -> list[str]:
    subnets: set[str] = set()
    for command in (["ip", "addr"], ["ifconfig"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2)
        except Exception:
            continue
        ips = re.findall(r"inet (?:addr:)?(\d+\.\d+\.\d+)\.\d+", result.stdout)
        for prefix in ips:
            if not prefix.startswith("127."):
                subnets.add(f"{prefix}.0/24")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
            prefix = ".".join(local_ip.split(".")[:3])
            subnets.add(f"{prefix}.0/24")
    except Exception:
        pass
    return sorted(subnets) or ["192.168.1.0/24"]


async def probe_port(ip: str, port: int) -> bool:
    settings = get_settings()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=settings.scan_connect_timeout_seconds
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def probe_host(ip: str) -> tuple[str, list[int]] | None:
    ports = get_settings().scan_port_list
    results = await asyncio.gather(*(probe_port(ip, port) for port in ports))
    open_ports = [port for port, is_open in zip(ports, results, strict=True) if is_open]
    return (ip, open_ports) if open_ports else None


async def fingerprint_printer(ip: str, ports_open: list[int]) -> dict:
    info = {
        "ip": ip,
        "ports_open": ports_open,
        "is_online": True,
        "last_seen_at": datetime.now(timezone.utc),
    }
    if 9100 not in ports_open:
        return info

    try:
        hi = await query_zpl(ip)
        if hi:
            parts = hi.split(",")
            if parts:
                info["product_name"] = parts[0].split("-")[0]
            if len(parts) > 1:
                info["firmware"] = parts[1]
    except Exception:
        pass

    for var, field in {
        "device.friendly_name": "friendly_name",
        "device.product_name": "product_name",
        "device.printhead.resolution": "_dpi_raw",
        "zpl.dots_per_mm": "_dpmm_raw",
        "ezpl.print_width": "_print_width_raw",
        "ezpl.label_length": "_label_length_raw",
        "media.type": "media_type",
        "media.out": "_media_out_raw",
        "odometer.total_label_count": "odometer",
    }.items():
        try:
            value = await query_sgd(ip, var)
            if value:
                info[field] = value.strip().strip('"')
        except Exception:
            pass

    # Resolve DPI first so we can convert dots
    dpi_raw = info.pop("_dpi_raw", None)
    dpmm_raw = info.pop("_dpmm_raw", None)
    dpi = _resolve_dpi(dpi_raw, dpmm_raw)
    if dpi:
        info["dpi"] = dpi
    if dpi_raw:
        info["resolution"] = dpi_raw
    elif dpi:
        info["resolution"] = f"{dpi} dpi"

    for raw_field, out_field in (("_print_width_raw", "print_width"), ("_label_length_raw", "label_length")):
        raw = info.pop(raw_field, None)
        if raw:
            info[out_field] = _dots_to_mm(raw, dpi) if dpi else raw

    # Normalise media_out to a bool
    raw = info.pop("_media_out_raw", None)
    if raw is not None:
        info["media_out"] = raw.lower() == "yes"

    return info


async def scan_subnet(subnet: str) -> list[dict]:
    settings = get_settings()
    network = ipaddress.IPv4Network(subnet, strict=False)
    semaphore = asyncio.Semaphore(settings.scanner_workers)

    async def bounded_probe(ip: str) -> tuple[str, list[int]] | None:
        async with semaphore:
            return await probe_host(ip)

    results = await asyncio.gather(*(bounded_probe(str(ip)) for ip in network.hosts()))
    found = [result for result in results if result]
    return await asyncio.gather(*(fingerprint_printer(ip, ports) for ip, ports in found))


async def scan_all(subnets: list[str] | None = None) -> list[dict]:
    subnets = subnets or get_settings().scan_subnet_list or get_local_subnets()
    results = await asyncio.gather(*(scan_subnet(subnet) for subnet in subnets))
    return [printer for subnet_results in results for printer in subnet_results]
