import asyncio
from collections import defaultdict

from app.config import get_settings


_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def send_zpl(ip: str, zpl: bytes) -> None:
    settings = get_settings()
    async with _locks[ip]:
        last_error: Exception | None = None
        attempts = settings.print_retry_count + 1
        for _ in range(attempts):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, settings.printer_port),
                    timeout=settings.printer_connect_timeout_seconds,
                )
                writer.write(zpl)
                await asyncio.wait_for(writer.drain(), timeout=settings.printer_write_timeout_seconds)
                writer.close()
                await writer.wait_closed()
                return
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.2)
        raise RuntimeError(f"Failed to send to {ip}: {last_error}")


async def query_zpl(ip: str, command: bytes = b"~HI") -> str:
    settings = get_settings()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, settings.printer_port),
        timeout=settings.printer_status_timeout_seconds,
    )
    writer.write(command)
    await writer.drain()
    data = b""
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=settings.printer_read_timeout_seconds)
            if not chunk:
                break
            data += chunk
    except asyncio.TimeoutError:
        pass
    finally:
        writer.close()
        await writer.wait_closed()
    return data.decode("utf-8", errors="ignore").strip()


async def query_sgd(ip: str, var: str) -> str | None:
    response = await query_zpl(ip, f'! U1 getvar "{var}"\n'.encode())
    return response or None
