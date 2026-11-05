import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant

from custom_components.bridge_heat.const import DOMAIN
from custom_components.bridge_heat.coordinator import BridgeHeatRuntime


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    instance.config.latitude = 32.1
    instance.config.longitude = 34.8
    instance.data[DOMAIN] = {}
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "recorder.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE states_meta (metadata_id INTEGER PRIMARY KEY, entity_id TEXT);
            CREATE TABLE state_attributes (attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT);
            CREATE TABLE states (state_id INTEGER PRIMARY KEY, metadata_id INTEGER,
                attributes_id INTEGER, state TEXT, last_updated_ts REAL);
            CREATE INDEX ix_states_last_updated_ts ON states(last_updated_ts);
        """)
        conn.execute("INSERT INTO states_meta VALUES (1, ?)", ("sensor.room_temperature",))
        conn.execute(
            "INSERT INTO state_attributes VALUES (1, ?)",
            (
                json.dumps(
                    {
                        "device_class": "temperature",
                        "unit_of_measurement": "°C",
                        "friendly_name": "Private bedroom",
                        "latitude": 32.1,
                        "secret": "private",
                    }
                ),
            ),
        )
    return path


def add_rows(path, count, timestamp, *, first_id=1, metadata_id=1, attributes_id=1):
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO states VALUES (?, ?, ?, ?, ?)",
            [
                (i, metadata_id, attributes_id, str(i), timestamp)
                for i in range(first_id, first_id + count)
            ],
        )


@pytest_asyncio.fixture
async def runtime(hass, database):
    entry = SimpleNamespace(entry_id="test-entry", options={}, data={})
    client = SimpleNamespace(
        send_data=AsyncMock(),
        check_pending_uploads=AsyncMock(return_value=[]),
        acknowledge_upload=AsyncMock(),
    )
    runtime = BridgeHeatRuntime(hass, entry, client, str(database))
    await runtime.async_load()
    yield runtime
    await runtime.shutdown()
    await asyncio.sleep(0)
