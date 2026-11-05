"""Bounded HTTP transport for the existing Bridge Heat API."""

import asyncio
import gzip
import json
import math
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import aiohttp

from .const import ACKNOWLEDGE_URL, PAGE_SIZE, PENDING_UPLOADS_URL, URL

MAX_RESPONSE_BYTES = 65536
MAX_RETRY_DELAY = 60


class UploadError(Exception):
    """A safe, credential-free error suitable for integration status."""


def retry_delay(value, attempt):
    """Honor both Retry-After forms without allowing an unbounded sleep."""
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 2**attempt
    if not math.isfinite(delay):
        delay = 2**attempt
    return max(0, delay) + random.uniform(0, 1)


class BridgeHeatClient:
    def __init__(self, session, api_key):
        # HA owns and closes this session; certificate verification stays enabled.
        self.session = session
        self.api_key = api_key
        self.retry_not_before = 0

    async def _request(self, method, url, *, upload=False, **kwargs):
        if not self.api_key:
            raise UploadError("Configure an API credential before uploading")
        if time.monotonic() < self.retry_not_before:
            raise UploadError("API rate limited; retry later")
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        headers.update(kwargs.pop("headers", {}))
        for attempt in range(3):
            retry_after = None
            try:
                async with self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                    # Never forward the credential to a redirect destination.
                    allow_redirects=False,
                    **kwargs,
                ) as response:
                    status = response.status
                    if status in (200, 201):
                        # Read a capped body even if the server omits Content-Length.
                        try:
                            body = await response.content.readexactly(MAX_RESPONSE_BYTES + 1)
                        except asyncio.IncompleteReadError as err:
                            body = err.partial
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise UploadError("API response exceeds the supported size")
                        return body
                    if status == 429 or (not upload and status in (500, 502, 503, 504)):
                        retry_after = response.headers.get("Retry-After")
                    else:
                        # Bodies/exception strings may echo keys or household data.
                        raise UploadError(f"API request failed (HTTP {status})")
            except (aiohttp.ClientSSLError, aiohttp.ServerFingerprintMismatch):
                raise UploadError("TLS certificate verification failed") from None
            except aiohttp.ClientConnectorError:
                pass  # Connection establishment failed before any upload was accepted.
            except (TimeoutError, aiohttp.ClientError):
                if upload:
                    # Retain the durable batch; automatic POST retries can duplicate data.
                    raise UploadError("Upload outcome unknown; retained batch may replay") from None
            delay = retry_delay(retry_after, attempt + 1)
            if delay > MAX_RETRY_DELAY:
                # Honor a long Retry-After without holding a worker asleep. The
                # process-local cooldown also applies to subsequent poll attempts.
                self.retry_not_before = time.monotonic() + delay
                raise UploadError("API rate limited; retry later")
            if attempt < 2:
                await asyncio.sleep(delay)
        raise UploadError("API unavailable after bounded retries")

    async def send_data(self, samples, *, device_id, location=None):
        if not samples or len(samples) > PAGE_SIZE:
            raise UploadError("Upload batch must contain 1–500 samples")
        payload = {"samples": samples, "device_id": device_id}
        if location is not None:
            payload["location"] = location
        # A single bounded batch avoids retrying earlier successful chunks.
        compressed = await asyncio.to_thread(
            lambda: gzip.compress(json.dumps(payload, allow_nan=False).encode())
        )
        await self._request(
            "POST",
            URL,
            upload=True,
            data=compressed,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

    async def check_pending_uploads(self, *, device_id, location=None):
        params = {"device_id": device_id}
        if location is not None:
            params["location"] = location
        body = await self._request("GET", PENDING_UPLOADS_URL, params=params)
        try:
            data = json.loads(body)
            requests = data["requests"]
            if not isinstance(requests, list) or len(requests) > 100:
                raise ValueError
            if any(not isinstance(r, dict) or type(r.get("id")) is not int for r in requests):
                raise ValueError
            return requests
        except (ValueError, KeyError, TypeError):
            # A broken poll must not be presented as an empty successful poll.
            raise UploadError("Invalid pending-upload response") from None

    async def acknowledge_upload(self, request_id, status, notes=None):
        payload = {"status": status}
        if notes:
            payload["notes"] = notes
        await self._request("POST", f"{ACKNOWLEDGE_URL}/{int(request_id)}", json=payload)
