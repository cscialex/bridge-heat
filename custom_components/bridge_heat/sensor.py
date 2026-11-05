"""Event-driven upload status."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, STATUS_SIGNAL


async def async_setup_entry(hass, entry, async_add_entities):
    # Preserve the existing entity when upgrading the formerly shared unique ID.
    registry = er.async_get(hass)
    old_id = registry.async_get_entity_id("sensor", DOMAIN, "bridge_heat")
    if old_id and registry.async_get(old_id).config_entry_id == entry.entry_id:
        registry.async_update_entity(old_id, new_unique_id=f"{entry.entry_id}_status")
    async_add_entities([BridgeHeatSensor(hass.data[DOMAIN][entry.entry_id], entry)])


class BridgeHeatSensor(SensorEntity):
    _attr_name = "Bridge Heat"
    _attr_should_poll = False

    def __init__(self, runtime, entry):
        self.runtime = runtime
        # Each config entry needs a distinct registry identity.
        self._attr_unique_id = f"{entry.entry_id}_status"
        self.entry_id = entry.entry_id

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Non-polling entities must publish status changes explicitly.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{STATUS_SIGNAL}_{self.entry_id}",
                self.async_write_ha_state,
            )
        )

    @property
    def native_value(self):
        return self.runtime.status

    @property
    def extra_state_attributes(self):
        return {
            "samples": self.runtime.sample_count,
            "last_upload": self.runtime.last_upload,
            "last_error": self.runtime.last_error,
        }
