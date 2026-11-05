import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

from homeassistant.util import dt as dt_util

from custom_components.bridge_heat.const import PAGE_SIZE, SHARE_LOCATION, TEMP
from custom_components.bridge_heat.coordinator import BridgeHeatRuntime
from custom_components.bridge_heat.uploader import UploadError

from .conftest import add_rows


async def restart(runtime):
    other = BridgeHeatRuntime(runtime.hass, runtime.entry, runtime.client, runtime.db_path)
    await other.async_load()
    return other


async def test_outage_restart_and_partial_page_failure(runtime, database):
    # The saved cursor can be older than 24h; don't clamp it to today's window.
    old = dt_util.utcnow().timestamp() - 3 * 86400
    state = deepcopy(runtime.state)
    state["cursor"] = [old - 1, 0]
    await runtime.persist(state)
    add_rows(database, PAGE_SIZE * 2 + 1, old)
    runtime.client.send_data.side_effect = [None, UploadError("outage")]
    assert (await runtime.upload())[0] is False
    assert runtime.state["cursor"] == [old, PAGE_SIZE]
    assert len(runtime.state["pending"]["samples"]) == PAGE_SIZE
    runtime.client.send_data.reset_mock(side_effect=True)
    resumed = await restart(runtime)
    assert resumed.state["device_id"] == runtime.state["device_id"]
    assert (await resumed.upload())[:2] == (True, PAGE_SIZE + 1)
    assert runtime.client.send_data.call_count == 2
    assert runtime.client.send_data.call_args_list[0].args[0][0]["state"] == str(PAGE_SIZE + 1)
    assert resumed.state["pending"] is None


async def test_withdraw_consent_removes_buffer_before_retry(runtime, database):
    old = dt_util.utcnow().timestamp() - 600
    add_rows(database, 1, old)
    runtime.client.send_data.side_effect = UploadError("offline")
    await runtime.upload()
    assert runtime.state["pending"]["samples"][0]["location"]
    runtime.entry.options = {SHARE_LOCATION: False}
    resumed = await restart(runtime)
    assert "location" not in resumed.state["pending"]["samples"][0]
    runtime.entry.options[TEMP] = False
    resumed = await restart(resumed)
    assert resumed.state["pending"]["samples"] == []
    runtime.client.send_data.reset_mock()
    assert (await resumed.upload())[0]
    runtime.client.send_data.assert_not_called()


async def test_single_flight_and_cancellation_safe_shutdown(runtime, database):
    add_rows(database, 1, dt_util.utcnow().timestamp() - 600)
    started = asyncio.Event()

    async def blocking(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    runtime.client.send_data.side_effect = blocking
    task = asyncio.create_task(runtime.upload())
    await started.wait()
    assert (await runtime.upload())[0] is False
    await runtime.poll()
    runtime.client.check_pending_uploads.assert_not_called()
    await runtime.shutdown()
    assert task.cancelled()
    assert not runtime.tasks
    assert runtime.state["pending"] is not None
    assert not (await runtime.upload())[0]


async def test_terminal_ack_failure_survives_restart_without_reupload(runtime, database):
    add_rows(database, 1, dt_util.utcnow().timestamp() - 600)
    runtime.client.check_pending_uploads.return_value = [{"id": 12}]
    runtime.client.acknowledge_upload.side_effect = [None, UploadError("ack failed")]
    await runtime.poll()
    assert runtime.status == "Command failed"
    assert runtime.state["command"]["phase"] == "completed"
    assert runtime.client.send_data.call_count == 1
    runtime.client.acknowledge_upload.reset_mock(side_effect=True)
    resumed = await restart(runtime)
    await resumed.poll()
    assert resumed.state["command"] is None
    assert runtime.client.send_data.call_count == 1
    assert runtime.client.check_pending_uploads.call_count == 1
    assert runtime.client.acknowledge_upload.call_args.args[:2] == (12, "completed")


async def test_initial_ack_failure_prevents_upload(runtime):
    runtime.client.check_pending_uploads.return_value = [{"id": 12}]
    runtime.client.acknowledge_upload.side_effect = UploadError("ack failed")
    await runtime.poll()
    runtime.client.send_data.assert_not_called()
    assert runtime.state["command"]["phase"] == "acknowledged"
    assert runtime.status == "Command failed"


async def test_poll_failure_is_visible(runtime):
    runtime.client.check_pending_uploads.side_effect = UploadError("poll failed")
    await runtime.poll()
    assert runtime.last_error == "poll failed"
    assert runtime.status == "Command failed"


async def test_fetch_failure_does_not_complete_command_or_advance_cursor(runtime, database):
    runtime.db_path = str(database.parent / "missing.db")
    before = deepcopy(runtime.state)
    assert (await runtime.upload())[0] is False
    assert runtime.state == before
    assert runtime.status == "Upload failed"


async def test_row_added_during_post_is_not_skipped(runtime, database):
    now = dt_util.utcnow().timestamp()
    add_rows(database, 1, now - 1000)

    async def insert(*args, **kwargs):
        if runtime.client.send_data.call_count == 1:
            add_rows(database, 1, now - 500, first_id=2)

    runtime.client.send_data.side_effect = insert
    assert (await runtime.upload())[:2] == (True, 2)
    assert runtime.state["cursor"] == [now - 500, 2]


async def test_storage_failure_does_not_post_unbuffered_samples(runtime, database, monkeypatch):
    add_rows(database, 1, dt_util.utcnow().timestamp() - 600)
    monkeypatch.setattr(runtime.store, "async_save", AsyncMock(side_effect=OSError("private URL")))
    assert not (await runtime.upload())[0]
    runtime.client.send_data.assert_not_called()
    assert runtime.last_error == "Recorder or storage operation failed"


async def test_pending_batch_keeps_original_location_on_retry(runtime, database):
    add_rows(database, 1, dt_util.utcnow().timestamp() - 600)
    runtime.client.send_data.side_effect = UploadError("offline")
    await runtime.upload()
    original = runtime.state["pending"]["location"]
    runtime.hass.config.latitude = 10
    runtime.client.send_data.reset_mock(side_effect=True)
    assert (await runtime.upload())[0]
    assert runtime.client.send_data.call_args.kwargs["location"] == original
    assert runtime.client.send_data.call_args.args[0][0]["location"] == original


async def test_work_budget_commits_progress_and_continues(runtime, database, monkeypatch):
    from custom_components.bridge_heat import coordinator

    monkeypatch.setattr(coordinator, "MAX_PAGES", 1)
    add_rows(database, PAGE_SIZE + 1, dt_util.utcnow().timestamp() - 600)
    assert (await runtime.upload())[:2] == (False, PAGE_SIZE)
    assert runtime.status == "Catching up"
    assert runtime.state["pending"] is None
    assert (await runtime.upload())[:2] == (False, 1)
    assert (await runtime.upload())[:2] == (True, 0)


async def test_accepted_batch_with_failed_cursor_write_is_retained(runtime, database, monkeypatch):
    add_rows(database, 1, dt_util.utcnow().timestamp() - 600)
    original_cursor = list(runtime.state["cursor"])
    save = runtime.store.async_save
    writes = 0

    async def fail_commit(state):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("disk full after acceptance")
        await save(state)

    monkeypatch.setattr(runtime.store, "async_save", fail_commit)
    assert not (await runtime.upload())[0]
    assert runtime.client.send_data.call_count == 1
    assert runtime.state["cursor"] == original_cursor
    assert runtime.state["pending"] is not None
    resumed = await restart(runtime)
    assert resumed.state["pending"] == runtime.state["pending"]
    # Replay after this ambiguity is documented, rather than claiming exactly-once.
