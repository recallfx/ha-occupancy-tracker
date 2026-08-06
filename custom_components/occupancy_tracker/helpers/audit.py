"""Structured audit events for occupancy decisions."""

from __future__ import annotations

import json
import logging
import time
from typing import Any


AUDIT_LOGGER_NAME = "custom_components.occupancy_tracker.audit"
_LOGGER = logging.getLogger(AUDIT_LOGGER_NAME)


def audit_event(event: str, timestamp: float | None = None, **fields: Any) -> None:
    """Write one stable JSON object per auditable event."""
    payload = {
        "event": event,
        "timestamp": timestamp if timestamp is not None else time.time(),
        **fields,
    }
    _LOGGER.info(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    )
