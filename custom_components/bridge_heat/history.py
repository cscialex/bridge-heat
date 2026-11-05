"""Read bounded pages from the supported local SQLite Recorder schema."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .const import AQ, HUMIDITY, MAX_ATTRIBUTE_CHARS, PAGE_SIZE, PRESSURE, TEMP

EXCLUDED_KEYWORDS = (
    "battery",
    "cpu",
    "processor",
    "memory",
    "disk",
    "swap",
    "load",
    "storage",
    "ram",
    "gpu",
    "voltage",
    "current",
    "power",
    "signal",
    "rssi",
    "linkquality",
    "illuminance",
    "energy",
    "consumption",
    "uptime",
    "ping",
    "latency",
    "brightness",
    "speed",
    "volume",
    "valve",
    "co2",
    "voc",
    "backup",
    "attempt",
    "timestamp",
)
TEMP_UNITS = {"°c", "°f", "c", "f", "k"}
PRESSURE_UNITS = {"psi", "pa", "kpa", "hpa", "mmhg", "inhg", "bar", "mbar"}
SAFE_ATTRIBUTES = ("device_class", "unit_of_measurement", "state_class")


def sensor_category(entity_id, attributes):
    ent = entity_id.lower()
    if not ent.startswith("sensor.") or any(word in ent for word in EXCLUDED_KEYWORDS):
        return None
    dc = str(attributes.get("device_class") or "").lower()
    unit = str(attributes.get("unit_of_measurement") or "").strip().lower()
    if dc == "temperature" or (
        any(w in ent for w in ("_temp", ".temp", "temperature")) and unit in TEMP_UNITS
    ):
        return TEMP
    if dc == "humidity" or (any(w in ent for w in ("_hum", ".hum", "humidity")) and unit == "%"):
        return HUMIDITY
    if dc == "pressure" or (
        any(w in ent for w in ("_press", ".press", "pressure", "barometer"))
        and unit in PRESSURE_UNITS
    ):
        return PRESSURE
    if dc == "aqi" or "aqi" in ent or "air_quality" in ent:
        return AQ
    return None


def permitted(entity_id, attributes, options):
    category = sensor_category(entity_id, attributes)
    # Apply the same consent check to historical rows and durable retry batches.
    return category is not None and options.get(category, category != AQ)


def read_page(db_path, cursor, upper, options, location):
    """Return samples, scanned watermark and row count, with a fixed memory bound."""
    # SQLite's connection context manager commits but does not close connections.
    # SQLAlchemy accepts relative SQLite paths; resolve against the same process cwd.
    uri = Path(db_path).absolute().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        # Fail visibly for incompatible Recorder schemas instead of claiming no data.
        required = {
            "states": {"state_id", "metadata_id", "attributes_id", "state", "last_updated_ts"},
            "states_meta": {"metadata_id", "entity_id"},
            "state_attributes": {"attributes_id", "shared_attrs"},
        }
        for table, columns in required.items():
            present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns <= present:
                raise ValueError("Unsupported Recorder schema; upload cursor unchanged")
        # Keyset pagination preserves equal timestamps without OFFSET or unbounded fetchall.
        rows = conn.execute(
            """
            SELECT s.state_id, s.last_updated_ts, CASE WHEN length(s.state) <= 255 THEN s.state END AS state,
                   substr(m.entity_id, 1, 255) AS entity_id,
                   CASE WHEN length(a.shared_attrs) <= ? THEN a.shared_attrs END AS attrs
            FROM states s JOIN states_meta m ON m.metadata_id = s.metadata_id
            LEFT JOIN state_attributes a ON a.attributes_id = s.attributes_id
            WHERE s.last_updated_ts >= ?
              AND (s.last_updated_ts > ? OR s.state_id > ?)
              AND s.last_updated_ts <= ?
            ORDER BY s.last_updated_ts, s.state_id LIMIT ?
        """,
            (MAX_ATTRIBUTE_CHARS, cursor[0], cursor[0], cursor[1], upper, PAGE_SIZE),
        )
        samples = []
        scanned = 0
        watermark = list(cursor)
        for row in rows:
            scanned += 1
            watermark = [row["last_updated_ts"], row["state_id"]]
            try:
                attrs = json.loads(row["attrs"])
            except (ValueError, TypeError):
                continue
            if not isinstance(attrs, dict) or not permitted(row["entity_id"], attrs, options):
                continue
            if row["state"] in (None, "unknown", "unavailable", ""):
                continue
            # Preserve research units/classes, but omit unrelated free-form attributes.
            safe_attrs = {
                key: attrs[key]
                for key in SAFE_ATTRIBUTES
                if isinstance(attrs.get(key), str) and len(attrs[key]) <= 128
            }
            sample = {
                "entity": row["entity_id"],
                "attributes": safe_attrs,
                "state": row["state"],
                "time": datetime.fromtimestamp(row["last_updated_ts"], UTC).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
            if location is not None:
                sample["location"] = location
            samples.append(sample)
        # Keep the timestamp/state-id watermark at the last actual row. Advancing to
        # wall-clock upload completion would skip rows recorded during transmission.
        return samples, watermark, scanned
