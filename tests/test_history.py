import json
import sqlite3

import pytest

from custom_components.bridge_heat.const import AQ, HUMIDITY, PAGE_SIZE, PRESSURE, TEMP
from custom_components.bridge_heat.history import permitted, read_page

from .conftest import add_rows


@pytest.mark.parametrize(
    "category,device_class",
    [(TEMP, "temperature"), (HUMIDITY, "humidity"), (PRESSURE, "pressure"), (AQ, "aqi")],
)
def test_each_consent_overrides_history_names(category, device_class):
    attrs = {"device_class": device_class}
    assert not permitted("sensor.environment", attrs, {category: False})
    assert permitted("sensor.environment", attrs, {category: True})


def test_equal_timestamp_pagination_and_attribute_allowlist(database):
    add_rows(database, PAGE_SIZE + 1, 100)
    samples, cursor, scanned = read_page(str(database), [0, 0], 100, {}, None)
    assert scanned == len(samples) == PAGE_SIZE
    assert cursor == [100, PAGE_SIZE]
    assert samples[0]["attributes"] == {"device_class": "temperature", "unit_of_measurement": "°C"}
    assert "location" not in samples[0]
    second, cursor, scanned = read_page(str(database), cursor, 100, {}, None)
    assert scanned == len(second) == 1
    assert second[0]["state"] == str(PAGE_SIZE + 1)
    assert cursor == [100, PAGE_SIZE + 1]


def test_watermark_includes_filtered_rows_and_respects_upper(database):
    add_rows(database, 2, 100)
    add_rows(database, 1, 200, first_id=3)
    samples, cursor, scanned = read_page(str(database), [0, 0], 150, {TEMP: False}, "location")
    assert samples == [] and scanned == 2 and cursor == [100, 2]


def test_malformed_oversized_attributes_and_invalid_states(database):
    with sqlite3.connect(database) as conn:
        for i, attrs in enumerate(
            ["{", "[]", json.dumps({"device_class": "temperature", "large": "x" * 9000})], 2
        ):
            conn.execute("INSERT INTO state_attributes VALUES (?, ?)", (i, attrs))
    for i in range(2, 5):
        add_rows(database, 1, 100, first_id=i, attributes_id=i)
    add_rows(database, 1, 100, first_id=5)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE states SET state='unavailable' WHERE state_id=5")
    assert read_page(str(database), [0, 0], 100, {}, None) == ([], [100, 5], 4)


def test_schema_failure_closes_connection(database):
    with sqlite3.connect(database) as conn:
        conn.execute("DROP TABLE state_attributes")
    with pytest.raises(ValueError, match="Unsupported Recorder schema"):
        read_page(str(database), [0, 0], 100, {}, None)
    # A fresh writer can take an exclusive lock after the failed read.
    with sqlite3.connect(database, timeout=0) as conn:
        conn.execute("BEGIN EXCLUSIVE")


def test_relative_sqlite_path(database, monkeypatch):
    add_rows(database, 1, 100)
    monkeypatch.chdir(database.parent)
    samples, cursor, scanned = read_page(database.name, [0, 0], 100, {}, None)
    assert len(samples) == scanned == 1
    assert cursor == [100, 1]
