import asyncio
import ipaddress
import re
import socket
import subprocess
from datetime import datetime, timezone

from app.config import get_settings
from app.services.printer_io import query_sgd, query_zpl


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
        "ezpl.print_width": "print_width",
        "ezpl.label_length": "label_length",
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
