from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.bridge_heat import async_unload_entry
from custom_components.bridge_heat.config_flow import (
    ConfigFlow,
    OptionsFlowHandler,
    permissions_schema,
)
from custom_components.bridge_heat.const import API_KEY, DOMAIN, TEMP
from custom_components.bridge_heat.sensor import BridgeHeatSensor


async def test_setup_requires_credential_and_never_echoes_it():
    flow = ConfigFlow()
    flow.context = {}
    result = await flow.async_step_user({API_KEY: "   "})
    assert result["errors"] == {API_KEY: "credential_required"}
    result = await flow.async_step_user({API_KEY: " test-only-key ", TEMP: False})
    assert result["data"] == {API_KEY: "test-only-key"}
    assert result["options"] == {TEMP: False}
    assert "test-only-key" not in str(permissions_schema({API_KEY: "test-only-key"}))


async def test_blank_options_key_retains_existing_credential():
    flow = OptionsFlowHandler()
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()))
    result = await flow.async_step_options({API_KEY: " ", TEMP: False})
    assert result["data"] == {TEMP: False}
    flow.hass.config_entries.async_update_entry.assert_not_called()


async def test_unload_failure_keeps_runtime(hass, runtime):
    hass.data[DOMAIN][runtime.entry.entry_id] = runtime
    hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=False))
    assert not await async_unload_entry(hass, runtime.entry)
    assert not runtime.closing
    assert hass.data[DOMAIN][runtime.entry.entry_id] is runtime


async def test_successful_unload_cleans_up_before_removing_runtime(hass, runtime):
    hass.data[DOMAIN][runtime.entry.entry_id] = runtime
    remove = Mock()
    runtime.removers.append(remove)
    hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
    assert await async_unload_entry(hass, runtime.entry)
    assert runtime.closing
    remove.assert_called_once()
    assert runtime.entry.entry_id not in hass.data[DOMAIN]


async def test_status_publishes_and_unique_ids_differ(hass, runtime):
    first = BridgeHeatSensor(runtime, runtime.entry)
    second = BridgeHeatSensor(runtime, SimpleNamespace(entry_id="other"))
    assert first.unique_id != second.unique_id
    first.hass = hass
    first.async_write_ha_state = Mock()
    await first.async_added_to_hass()
    runtime.update("Upload failed", "safe error")
    await hass.async_block_till_done()
    first.async_write_ha_state.assert_called_once()
    assert first.native_value == "Upload failed"
    assert first.extra_state_attributes["last_error"] == "safe error"
    await first.async_will_remove_from_hass()


async def test_credential_rotation_commits_consent_in_same_update():
    entry = SimpleNamespace(data={API_KEY: "old-test-key"})
    update = Mock()
    flow = OptionsFlowHandler()
    flow.handler = "entry-id"
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=Mock(return_value=entry),
            async_update_entry=update,
        )
    )
    result = await flow.async_step_options({API_KEY: " replacement-test-key ", TEMP: False})
    assert result["data"] == {TEMP: False}
    assert update.call_args.kwargs == {
        "data": {API_KEY: "replacement-test-key"},
        "options": {TEMP: False},
    }
