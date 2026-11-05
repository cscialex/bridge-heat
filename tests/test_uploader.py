import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from custom_components.bridge_heat.uploader import BridgeHeatClient, UploadError, retry_delay


class Response:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.headers = headers or {}
        self.content = Mock()
        self.content.readexactly = AsyncMock(side_effect=asyncio.IncompleteReadError(body, 65537))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def client(*responses):
    session = Mock()
    session.request.side_effect = responses
    return BridgeHeatClient(session, "test-only-key")


async def test_wire_contract_and_secure_session_defaults():
    transport = client(Response())
    samples = [
        {
            "entity": "sensor.room_temperature",
            "attributes": {"device_class": "temperature"},
            "state": "21",
            "time": "2026-11-05 00:00:00",
        }
    ]
    await transport.send_data(samples, device_id="ha-test", location="32.1, 34.8")
    args, kwargs = transport.session.request.call_args
    assert args == ("POST", "https://bridgeheat.mssm.edu/api/v1/env")
    assert "ssl" not in kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["X-API-Key"] == "test-only-key"
    assert kwargs["headers"]["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(kwargs["data"])) == {
        "samples": samples,
        "device_id": "ha-test",
        "location": "32.1, 34.8",
    }


async def test_429_retry_and_bounded_delay(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    transport = client(Response(429, headers={"Retry-After": "7"}), Response())
    await transport.send_data([{}], device_id="ha-test")
    assert transport.session.request.call_count == 2
    assert 7 <= sleep.call_args.args[0] <= 8
    assert 999999 <= retry_delay("999999", 1) <= 1000000
    assert 0 <= retry_delay(format_datetime(datetime.now(UTC) + timedelta(seconds=5)), 1) <= 6
    assert 2 <= retry_delay("bad", 1) <= 3


@pytest.mark.parametrize("failure", [TimeoutError(), aiohttp.ClientPayloadError("private")])
async def test_ambiguous_post_not_retried(failure):
    transport = client(failure)
    with pytest.raises(UploadError, match="outcome unknown"):
        await transport.send_data([{}], device_id="ha-test")
    assert transport.session.request.call_count == 1


@pytest.mark.parametrize("status", [301, 400, 401, 403, 500, 503])
async def test_rejected_or_ambiguous_http_post_not_retried(status):
    transport = client(Response(status, body=b"test-only-key"))
    with pytest.raises(UploadError, match=f"HTTP {status}") as err:
        await transport.send_data([{}], device_id="ha-test")
    assert "test-only-key" not in str(err.value)
    assert transport.session.request.call_count == 1


async def test_tls_failure_is_terminal():
    transport = client(aiohttp.ClientSSLError(Mock(), OSError("certificate failure")))
    with pytest.raises(UploadError, match="TLS certificate"):
        await transport.acknowledge_upload(1, "completed")
    assert transport.session.request.call_count == 1


async def test_poll_failure_is_not_empty_success(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    transport = client(Response(503), Response(503), Response(503))
    with pytest.raises(UploadError, match="bounded retries"):
        await transport.check_pending_uploads(device_id="ha-test")
    assert transport.session.request.call_count == 3


@pytest.mark.parametrize(
    "body", [b"{}", b"{", b"[]", b'{"requests":{}}', b'{"requests":[{"id":"12"}]}']
)
async def test_invalid_poll_body(body):
    transport = client(Response(body=body))
    with pytest.raises(UploadError, match="Invalid pending"):
        await transport.check_pending_uploads(device_id="ha-test")


async def test_response_and_batch_limits():
    response = Response()
    response.content.readexactly = AsyncMock(return_value=b"x" * 65537)
    transport = client(response)
    with pytest.raises(UploadError, match="exceeds"):
        await transport.check_pending_uploads(device_id="ha-test")
    with pytest.raises(UploadError, match="1–500"):
        await transport.send_data([{}] * 501, device_id="ha-test")


async def test_missing_key_never_sends_request():
    transport = BridgeHeatClient(Mock(), "")
    with pytest.raises(UploadError, match="credential"):
        await transport.check_pending_uploads(device_id="ha-test")
    transport.session.request.assert_not_called()


async def test_long_retry_after_defers_without_early_retry(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    transport = client(Response(429, headers={"Retry-After": "3600"}))
    for _ in range(2):
        with pytest.raises(UploadError, match="rate limited"):
            await transport.check_pending_uploads(device_id="ha-test")
    sleep.assert_not_called()
    assert transport.session.request.call_count == 1
