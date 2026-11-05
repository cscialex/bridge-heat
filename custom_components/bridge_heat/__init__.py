"""Bridge Heat environmental data integration."""

import random
from datetime import timedelta

from homeassistant.components import recorder
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util
from sqlalchemy.engine import make_url

from .const import API_KEY, DOMAIN, PLATFORMS, POLL_INTERVAL, UPLOAD_INTERVAL
from .coordinator import BridgeHeatRuntime
from .uploader import BridgeHeatClient


async def async_setup(hass: HomeAssistant, config):
    hass.data.setdefault(DOMAIN, {})

    async def handle_trigger_upload(call):
        for runtime in list(hass.data[DOMAIN].values()):
            await runtime.upload()

    hass.services.async_register(DOMAIN, "upload_data", handle_trigger_upload)
    return True


async def _reload_entry(hass, entry):
    # Reload atomically cancels old workers before new consent/credentials take effect.
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})
    try:
        # Follow Recorder's actual configured URL rather than silently reading an
        # unrelated default file. This reader intentionally supports local SQLite.
        db_url = make_url(recorder.get_instance(hass).db_url)
        if db_url.drivername != "sqlite" or not db_url.database or db_url.database == ":memory:":
            raise ValueError("Bridge Heat requires a local SQLite Recorder database")
        client = BridgeHeatClient(
            async_get_clientsession(hass),
            entry.options.get(API_KEY, entry.data.get(API_KEY, "")),
        )
        runtime = BridgeHeatRuntime(hass, entry, client, db_url.database)
        await runtime.async_load()
    except Exception:  # noqa: BLE001 - sanitize URLs and storage errors
        # Do not log connection URLs, credentials, or raw storage contents.
        raise ConfigEntryNotReady("Cannot initialize Bridge Heat Recorder/storage") from None

    hass.data[DOMAIN][entry.entry_id] = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await runtime.shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(_reload_entry))

    async def periodic(now):
        # Commands and local triggers share the same cursor and lock. Continue a
        # large backlog in bounded work units instead of waiting another full day.
        if runtime.catchup_remove is not None:
            runtime.catchup_remove()
            runtime.catchup_remove = None
        await runtime.upload(now)
        if runtime.status == "Catching up" and not runtime.closing:
            runtime.catchup_remove = async_call_later(hass, 60, run_periodic)

    async def start(now):
        runtime.removers.append(
            async_track_time_interval(
                hass,
                run_periodic,
                timedelta(seconds=UPLOAD_INTERVAL),
            )
        )
        await periodic(now)

    @callback
    def run_periodic(now):
        entry.async_create_background_task(hass, periodic(now), "Bridge Heat upload")

    @callback
    def run_poll(now):
        entry.async_create_background_task(hass, runtime.poll(now), "Bridge Heat commands")

    @callback
    def run_start(now):
        entry.async_create_background_task(hass, start(now), "Bridge Heat start")

    runtime.removers.append(
        async_track_time_interval(
            hass,
            run_poll,
            timedelta(seconds=POLL_INTERVAL),
        )
    )
    # Preserve the research deployment's randomized midnight-to-6am first upload.
    now = dt_util.now()
    target = now.replace(
        hour=random.randrange(6), minute=random.randrange(60), second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=1)
    delay = 60 if UPLOAD_INTERVAL <= 900 else (target - now).total_seconds()
    runtime.removers.append(async_call_later(hass, delay, run_start))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = hass.data[DOMAIN][entry.entry_id]
    # Keep the runtime usable when HA refuses to unload a platform.
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await runtime.shutdown()
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
