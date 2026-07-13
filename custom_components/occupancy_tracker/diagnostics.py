"""Diagnostic utilities for Occupancy Tracker."""

import time
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import OccupancyCoordinator


class OccupancyDiagnostics:
    """Diagnostic utilities for the occupancy tracking system."""

    def __init__(self, coordinator: "OccupancyCoordinator"):
        """Initialize diagnostics."""
        self.coordinator = coordinator

    def get_area_status(self, area_id: str) -> Dict[str, Any]:
        """Get detailed status information for an area."""
        area = self.coordinator.areas.get(area_id)
        if not area:
            return {"error": "Area not found"}

        now = time.time()
        return {
            "id": area_id,
            "name": area.config.get("name", area_id),
            "occupancy": area.occupancy,
            "last_motion": area.last_motion,
            "time_since_motion": now - area.last_motion
            if area.last_motion > 0
            else None,
            "indoors": area.is_indoors,
            "exit_capable": area.is_exit_capable,
            "adjacent_areas": self.coordinator.config.get("adjacency", {}).get(
                area_id, []
            ),
        }

    def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status information."""
        occupied_areas = [
            (area_id, area.occupancy)
            for area_id, area in self.coordinator.areas.items()
            if area.occupancy > 0
        ]

        return {
            "total_occupancy": sum(occ for _, occ in occupied_areas),
            "occupied_areas": dict(occupied_areas),
            "active_warnings": len(self.coordinator.get_warnings(active_only=True)),
            "last_event_time": self.coordinator.last_event_time,
            "uptime": time.time() - self.coordinator.last_event_time,
        }

    def diagnose_motion_issues(self, sensor_id: Optional[str] = None) -> Dict[str, Any]:
        """Diagnostic method to help identify why motion isn't being detected."""
        sensors_to_check = (
            [sensor_id] if sensor_id else list(self.coordinator.sensors.keys())
        )
        results = {}

        for s_id in sensors_to_check:
            if s_id not in self.coordinator.sensors:
                results[s_id] = {"error": "Sensor not found"}
                continue

            sensor = self.coordinator.sensors[s_id]
            sensor_type = sensor.config.get("type", "unknown")
            area_id = sensor.config.get("area")

            sensor_info = {
                "sensor_type": sensor_type,
                "is_motion_sensor": sensor_type
                in ["motion", "camera_motion", "camera_person"],
                "current_state": sensor.current_state,
                "is_available": sensor.is_available,
                "area_id": area_id,
                "area_exists": area_id in self.coordinator.areas if area_id else False,
                "history_length": len(sensor.history)
                if hasattr(sensor, "history")
                else "unknown",
                "is_reliable": sensor.is_reliable
                if hasattr(sensor, "is_reliable")
                else "unknown",
            }

            # Add area information if applicable
            if area_id and area_id in self.coordinator.areas:
                area = self.coordinator.areas[area_id]
                sensor_info["area_info"] = {
                    "occupancy": area.occupancy,
                    "last_motion": area.last_motion,
                    "time_since_motion": time.time() - area.last_motion
                    if area.last_motion > 0
                    else None,
                    "activity_history_length": len(area.activity_history),
                }

            results[s_id] = sensor_info

        return results
