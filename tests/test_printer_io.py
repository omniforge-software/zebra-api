"""Tests for printer_io: send_zpl and query_zpl with mocked sockets."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.printer_io import query_zpl, send_zpl


@pytest.fixture(autouse=True)
def _clear_locks():
    """Reset per-printer locks between tests."""
    from app.services import printer_io
    printer_io._locks.clear()


def _make_mock_connection(response: bytes = b""):
    """Return (reader, writer) mocks for asyncio.open_connection."""
    reader = AsyncMock()
    reader.read = AsyncMock(side_effect=[response, b""])

    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    return reader, writer


class TestSendZpl:
    @pytest.mark.asyncio
    async def test_successful_send(self):
        _, writer = _make_mock_connection()
        with patch("app.services.printer_io.asyncio.open_connection", AsyncMock(return_value=(None, writer))):
            await send_zpl("192.168.45.208", b"^XA^XZ")
        writer.write.assert_called_once_with(b"^XA^XZ")
        writer.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        writer_ok = MagicMock()
        writer_ok.write = MagicMock()
        writer_ok.drain = AsyncMock()
        writer_ok.close = MagicMock()
        writer_ok.wait_closed = AsyncMock()

        call_count = 0

        async def flaky_connect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionRefusedError("refused")
            return None, writer_ok

        with patch("app.services.printer_io.asyncio.open_connection", side_effect=flaky_connect):
            await send_zpl("192.168.45.208", b"^XA^XZ")
        assert call_count == 2
        writer_ok.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_fail(self):
        async def always_fail(*args, **kwargs):
            raise ConnectionRefusedError("refused")

        with patch("app.services.printer_io.asyncio.open_connection", side_effect=always_fail):
            with pytest.raises(RuntimeError, match="Failed to send"):
                await send_zpl("192.168.45.208", b"^XA^XZ")


class TestQueryZpl:
    @pytest.mark.asyncio
    async def test_returns_response(self):
        reader, writer = _make_mock_connection(b"ZT411-300dpi,V92.21.26Z,12,8176KB")
        with patch("app.services.printer_io.asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            result = await query_zpl("192.168.45.208")
        assert "ZT411" in result
        writer.write.assert_called_once_with(b"~HI")

    @pytest.mark.asyncio
    async def test_empty_response(self):
        reader, writer = _make_mock_connection(b"")
        with patch("app.services.printer_io.asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
            result = await query_zpl("192.168.45.208")
        assert result == ""
