"""Durable single-flight upload and command processing."""

import asyncio
import logging
from copy import deepcopy
from uuid import uuid4

from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    MAX_PAGES,
    PAGE_SIZE,
    RECORDER_SETTLE_SECONDS,
    SAMPLE_INTERVAL,
    SHARE_LOCATION,
    STATUS_SIGNAL,
)
from .history import permitted, read_page
from .uploader import UploadError

_LOGGER = logging.getLogger(__name__)


class BridgeHeatRuntime:
    def __init__(self, hass, entry, client, db_path):
        self.hass = hass
        self.entry = entry
        self.client = client
        self.db_path = db_path
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.state = None
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.removers = []
        self.catchup_remove = None
        self.closing = False
        self.status = "Waiting for first upload"
        self.last_error = None
        self.last_upload = None
        self.sample_count = 0

    async def async_load(self):
        state = await self.store.async_load()
        if state is None:
            # An installation-scoped random ID avoids transmitting hardware MACs.
            # A cloned HA storage directory still needs explicit identity reset.
            state = {
                "device_id": f"ha-{uuid4().hex}",
                "cursor": [dt_util.utcnow().timestamp() - SAMPLE_INTERVAL, 0],
                "pending": None,
                "command": None,
            }
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("device_id"), str)
            or not isinstance(state.get("cursor"), list)
            or len(state["cursor"]) != 2
            or not isinstance(state["cursor"][0], (int, float))
            or type(state["cursor"][1]) is not int
        ):
            raise ValueError("Invalid Bridge Heat storage")
        pending = state.get("pending")
        if pending and (
            not isinstance(pending, dict)
            or not isinstance(pending.get("samples"), list)
            or len(pending["samples"]) > PAGE_SIZE
        ):
            raise ValueError("Invalid Bridge Heat pending batch")
        if pending:
            pending["samples"] = self.consented_samples(pending["samples"])
            if self.location is None:
                pending["location"] = None
        await self.persist(state)

    def consented_samples(self, samples):
        result = []
        for sample in samples:
            if permitted(sample["entity"], sample["attributes"], self.entry.options):
                sample = dict(sample)
                if self.location is None:
                    sample.pop("location", None)
                result.append(sample)
        return result

    async def persist(self, state):
        # Complete HA's atomic write before cancellation can let unload finish.
        task = self.hass.async_create_task(self.store.async_save(state))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        self.state = state

    def update(self, status, error=None):
        self.status = status
        self.last_error = error
        async_dispatcher_send(self.hass, f"{STATUS_SIGNAL}_{self.entry.entry_id}")

    @property
    def location(self):
        # Location remains enabled for existing research installations; users can
        # withdraw it explicitly, including from already buffered retry samples.
        if self.entry.options.get(SHARE_LOCATION, True):
            return f"{self.hass.config.latitude}, {self.hass.config.longitude}"
        return None

    async def _drain(self, upper):
        total = 0
        for _ in range(MAX_PAGES):
            pending = self.state.get("pending")
            if pending is None:
                samples, cursor, scanned = await self.hass.async_add_executor_job(
                    read_page,
                    self.db_path,
                    self.state["cursor"],
                    upper,
                    dict(self.entry.options),
                    self.location,
                )
                if not scanned:
                    return True, total
                pending = {"samples": samples, "cursor": cursor, "location": self.location}
                state = deepcopy(self.state)
                state["pending"] = pending
                # Save the exact bounded batch before issuing a POST, so an outage
                # or restart cannot silently discard it or recalculate its contents.
                await self.persist(state)

            samples = self.consented_samples(pending["samples"])
            location = pending.get("location") if self.location is not None else None
            if samples != pending["samples"] or location != pending.get("location"):
                state = deepcopy(self.state)
                state["pending"]["samples"] = samples
                state["pending"]["location"] = location
                # Persist withdrawn consent before a network retry can fail again.
                await self.persist(state)
            self.sample_count = len(samples)
            self.update("Uploading")
            if samples:
                await self.client.send_data(
                    samples, device_id=self.state["device_id"], location=location
                )
            state = deepcopy(self.state)
            state["cursor"] = pending["cursor"]
            state["pending"] = None
            # Commit only this acknowledged page; later page failures never replay
            # already committed chunks. Acceptance followed by a lost response or
            # local write failure still requires server deduplication for certainty.
            await self.persist(state)
            total += len(samples)
            self.sample_count = 0
        return False, total

    async def upload(self, now=None):
        if self.closing or self.lock.locked():
            return False, 0, "Upload already running or integration unloading"
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with self.lock:
                # Include fetch/storage failures in the same visible error path.
                complete, total = await self._drain(
                    dt_util.utcnow().timestamp() - RECORDER_SETTLE_SECONDS
                )
                self.last_upload = dt_util.utcnow().isoformat()
                self.update("Idle" if complete else "Catching up")
                return complete, total, "Upload completed" if complete else "More history remains"
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - isolate callback failures from HA
            message = (
                str(err) if isinstance(err, UploadError) else "Recorder or storage operation failed"
            )
            self.update("Upload failed", message)
            _LOGGER.warning("Bridge Heat: %s", message)
            return False, 0, message
        finally:
            self.tasks.discard(task)

    async def poll(self, now=None):
        if self.closing or self.lock.locked():
            return
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with self.lock:
                command = self.state.get("command")
                if command is None:
                    pending = await self.client.check_pending_uploads(
                        device_id=self.state["device_id"],
                        location=self.location,
                    )
                    if not pending:
                        return
                    command = {
                        "id": pending[0]["id"],
                        "phase": "acknowledged",
                        "upper": dt_util.utcnow().timestamp() - RECORDER_SETTLE_SECONDS,
                        "count": 0,
                    }
                    state = deepcopy(self.state)
                    state["command"] = command
                    await self.persist(state)
                if command["phase"] == "acknowledged":
                    # Do not upload a command whose initial acknowledgement failed.
                    await self.client.acknowledge_upload(command["id"], "acknowledged")
                    state = deepcopy(self.state)
                    state["command"]["phase"] = "uploading"
                    await self.persist(state)
                    command = self.state["command"]
                if command["phase"] == "uploading":
                    complete, count = await self._drain(command["upper"])
                    state = deepcopy(self.state)
                    state["command"]["count"] += count
                    if complete:
                        state["command"]["phase"] = "completed"
                    await self.persist(state)
                    command = self.state["command"]
                    if not complete:
                        self.update("Catching up")
                        return
                # Persist terminal state before sending it. A failed final ACK is
                # retried after restart without performing the command's upload again.
                await self.client.acknowledge_upload(
                    command["id"],
                    "completed",
                    notes="Client catch-up completed; previously committed pages were not resent.",
                )
                state = deepcopy(self.state)
                state["command"] = None
                await self.persist(state)
                self.last_upload = dt_util.utcnow().isoformat()
                self.update("Idle")
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - isolate callback failures from HA
            message = (
                str(err) if isinstance(err, UploadError) else "Recorder or storage operation failed"
            )
            self.update("Command failed", message)
            _LOGGER.warning("Bridge Heat: %s", message)
        finally:
            self.tasks.discard(task)

    async def shutdown(self):
        self.closing = True
        for remove in self.removers:
            remove()
        self.removers.clear()
        if self.catchup_remove is not None:
            self.catchup_remove()
            self.catchup_remove = None
        # Cancel and await active callbacks before removing their shared runtime.
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
