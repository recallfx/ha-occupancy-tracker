"""Binary sensor for individual area occupancy."""

import time

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..coordinator import OccupancyCoordinator


class AreaOccupancyBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that is ON when an area is occupied.

    Attributes expose the evidence behind the conservative state.
    """

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coordinator: OccupancyCoordinator, area: str):
        super().__init__(coordinator)
        self._area = area
        area_name = coordinator.config["areas"][area].get("name", area)
        self._attr_name = f"{area_name} Occupancy"
        self._attr_unique_id = f"occupancy_{area}"

    @property
    def is_on(self) -> bool:
        """Return True if area is occupied."""
        return self.coordinator.get_occupancy(self._area) > 0

    @property
    def available(self) -> bool:
        """Unknown indoor state is not confident vacancy."""
        area_state = self.coordinator.areas.get(self._area)
        return super().available and bool(area_state and area_state.state_known)

    @property
    def extra_state_attributes(self):
        """Return occupancy evidence and freshness attributes."""
        area_state = self.coordinator.areas.get(self._area)
        if not area_state:
            return {}

        now = time.time()
        freshness = self.coordinator.get_occupancy_freshness(self._area, now)
        last_motion = area_state.last_motion
        time_since = round(now - last_motion) if last_motion > 0 else None

        return {
            "occupancy_count": area_state.occupancy,
            "evidence_state": self.coordinator.get_occupancy_evidence(self._area),
            "active_sensors": self.coordinator.get_active_sensor_ids(self._area),
            "freshness": round(freshness, 2),
            "probability": round(freshness, 2),
            "last_motion": last_motion if last_motion > 0 else None,
            "last_positive_evidence": last_motion if last_motion > 0 else None,
            "last_contact": area_state.last_contact or None,
            "last_activity": area_state.last_activity or None,
            "stale_since": area_state.stale_since,
            "cleared_by": area_state.cleared_by,
            "time_since_motion_s": time_since,
            "is_indoors": area_state.is_indoors,
            "is_exit_capable": area_state.is_exit_capable,
            "state_known": area_state.state_known,
            "room_profile": area_state.profile_name,
        }


class AreaActivityBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Short-lived room activity; never authoritative for safe occupancy."""

    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: OccupancyCoordinator, area: str):
        super().__init__(coordinator)
        self._area = area
        area_name = coordinator.config["areas"][area].get("name", area)
        self._attr_name = f"{area_name} Activity"
        self._attr_unique_id = f"activity_{area}"

    def _activity_source(self, now: float) -> str:
        if self.coordinator.get_active_sensor_ids(self._area):
            return "live"

        area = self.coordinator.areas.get(self._area)
        if area and area.last_activity > 0:
            age = now - area.last_activity
            if age <= area.profile.activity_hold_seconds:
                if area.last_contact > area.last_motion:
                    return "recent_contact"
                return "recent"
        return "none"

    @property
    def is_on(self) -> bool:
        """Return whether the room has live or recent activity."""
        return self._activity_source(time.time()) != "none"

    @property
    def extra_state_attributes(self):
        """Explain the short-lived activity signal and its safe counterpart."""
        area = self.coordinator.areas.get(self._area)
        if not area:
            return {}

        now = time.time()
        last_activity = area.last_activity
        age = max(0, round(now - last_activity)) if last_activity > 0 else None
        return {
            "activity_source": self._activity_source(now),
            "hold_seconds": area.profile.activity_hold_seconds,
            "last_activity": last_activity if last_activity > 0 else None,
            "activity_age_s": age,
            "safe_occupancy": area.occupied,
            "occupancy_evidence": self.coordinator.get_occupancy_evidence(self._area),
            "room_profile": area.profile_name,
        }
