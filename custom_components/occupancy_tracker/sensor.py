from homeassistant.components.sensor import SensorEntity

from .occupancy_tracker import OccupancyTracker
from .components.types import OccupancyTrackerConfig

from . import DOMAIN


async def async_setup_platform(
    hass, config: OccupancyTrackerConfig, async_add_entities
):
    """Set up the Occupancy Tracker sensors."""
    occupancy_tracker: OccupancyTracker = hass.data[DOMAIN]["occupancy_tracker"]
    sensors = []

    # Individual area sensors
    for area in occupancy_tracker.config["areas"]:
        sensors.append(OccupancyCountSensor(occupancy_tracker, area))
        sensors.append(OccupancyProbabilitySensor(occupancy_tracker, area))

    # Global sensors
    sensors.extend(
        [
            OccupiedInsideAreasSensor(occupancy_tracker),
            OccupiedOutsideAreasSensor(occupancy_tracker),
            TotalOccupantsInsideSensor(occupancy_tracker),
            TotalOccupantsOutsideSensor(occupancy_tracker),
            TotalOccupantsSensor(occupancy_tracker),
            AnomalySensor(occupancy_tracker),
        ]
    )

    async_add_entities(sensors, True)


class OccupancyCountSensor(SensorEntity):
    """Sensor for occupancy count."""

    def __init__(self, occupancy_tracker: OccupancyTracker, area: str):
        self._occupancy_tracker = occupancy_tracker
        self._area = area
        self._attr_name = f"Occupancy Count {area}"
        self._attr_unique_id = f"occupancy_count_{area}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._occupancy_tracker.get_occupancy(self._area)


class OccupancyProbabilitySensor(SensorEntity):
    """Sensor for occupancy probability."""

    def __init__(self, occupancy_tracker: OccupancyTracker, area: str):
        self._occupancy_tracker = occupancy_tracker
        self._area = area
        self._attr_name = f"Occupancy Probability {area}"
        self._attr_unique_id = f"occupancy_probability_{area}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._occupancy_tracker.get_occupancy_probability(self._area)


class AnomalySensor(SensorEntity):
    """Sensor for detected anomalies.

    Reports both the count of anomalies and detailed information including:
    - Anomaly type (stuck_sensor, unexpected_motion, simultaneous_motion, etc.)
    - Affected areas and sensors
    - Timestamps and durations
    - Additional context like occupancy counts
    """

    def __init__(self, occupancy_tracker: OccupancyTracker):
        self._occupancy_tracker = occupancy_tracker
        self._attr_name = "Detected Anomalies"
        self._attr_unique_id = "detected_anomalies"
        self._attr_icon = "mdi:alert-circle"

    @property
    def state(self):
        """Return the count of detected anomalies."""
        return len(self._occupancy_tracker.get_warnings(active_only=True))

    @property
    def extra_state_attributes(self):
        """Return detailed state attributes about anomalies.

        Returns:
            dict: Contains:
                - List of all anomalies with full details
                - Count of each anomaly type
                - Latest anomaly details
                - Affected areas/sensors summary
        """
        warnings = self._occupancy_tracker.get_warnings(active_only=True)

        # Convert warnings to dictionary representation
        anomalies = []
        for warning in warnings:
            anomaly = {
                "id": warning.id,
                "type": warning.type,
                "message": warning.message,
                "timestamp": warning.timestamp,
                "is_active": warning.is_active,
            }

            if warning.area:
                anomaly["area"] = warning.area
            if warning.sensor_id:
                anomaly["sensor"] = warning.sensor_id

            anomalies.append(anomaly)

        # Count anomalies by type
        type_counts = {}
        affected_areas = set()
        affected_sensors = set()

        for anomaly in anomalies:
            atype = anomaly.get("type", "unknown")
            type_counts[atype] = type_counts.get(atype, 0) + 1

            if "area" in anomaly:
                affected_areas.add(anomaly["area"])
            if "sensor" in anomaly:
                affected_sensors.add(anomaly["sensor"])

        return {
            "anomalies": anomalies,  # Full list of anomaly records
            "anomaly_counts": type_counts,  # Count by type
            "latest_anomaly": anomalies[-1]
            if anomalies
            else None,  # Most recent anomaly
            "affected_areas": list(affected_areas),  # List of areas with anomalies
            "affected_sensors": list(
                affected_sensors
            ),  # List of sensors with anomalies
            "total_affected_areas": len(affected_areas),
            "total_affected_sensors": len(affected_sensors),
        }

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return "problem"


class OccupiedInsideAreasSensor(SensorEntity):
    """Sensor that lists all occupied indoor areas."""

    def __init__(self, occupancy_tracker: OccupancyTracker):
        self._occupancy_tracker = occupancy_tracker
        self._attr_name = "Occupied Inside Areas"
        self._attr_unique_id = "occupied_inside_areas"

    @property
    def state(self):
        """Return the number of occupied indoor areas."""
        occupied_areas = [
            area
            for area, config in self._occupancy_tracker.config["areas"].items()
            if config.get("indoors", True)
            and self._occupancy_tracker.get_occupancy(area) > 0
        ]
        return len(occupied_areas)

    @property
    def extra_state_attributes(self):
        """Return the list of occupied indoor areas."""
        return {
            "areas": [
                area
                for area, config in self._occupancy_tracker.config["areas"].items()
                if config.get("indoors", True)
                and self._occupancy_tracker.get_occupancy(area) > 0
            ]
        }


class OccupiedOutsideAreasSensor(SensorEntity):
    """Sensor that lists all occupied outdoor areas."""

    def __init__(self, occupancy_tracker: OccupancyTracker):
        self._occupancy_tracker = occupancy_tracker
        self._attr_name = "Occupied Outside Areas"
        self._attr_unique_id = "occupied_outside_areas"

    @property
    def state(self):
        """Return the number of occupied outdoor areas."""
        occupied_areas = [
            area
            for area, config in self._occupancy_tracker.config["areas"].items()
            if not config.get("indoors", True)
            and self._occupancy_tracker.get_occupancy(area) > 0
        ]
        return len(occupied_areas)

    @property
    def extra_state_attributes(self):
        """Return the list of occupied outdoor areas."""
        return {
            "areas": [
                area
                for area, config in self._occupancy_tracker.config["areas"].items()
                if not config.get("indoors", True)
                and self._occupancy_tracker.get_occupancy(area) > 0
            ]
        }


class TotalOccupantsInsideSensor(SensorEntity):
    """Sensor for total number of occupants inside."""

    def __init__(self, occupancy_tracker: OccupancyTracker):
        self._occupancy_tracker = occupancy_tracker
        self._attr_name = "Total Occupants Inside"
        self._attr_unique_id = "total_occupants_inside"

    @property
    def state(self):
        """Return the total number of occupants in indoor areas."""
        return sum(
            self._occupancy_tracker.get_occupancy(area)
            for area, config in self._occupancy_tracker.config["areas"].items()
            if config.get("indoors", True)
        )


class TotalOccupantsOutsideSensor(SensorEntity):
    """Sensor for total number of occupants outside."""

    def __init__(self, occupancy_tracker: OccupancyTracker):
        self._occupancy_tracker = occupancy_tracker
        self._attr_name = "Total Occupants Outside"
        self._attr_unique_id = "total_occupants_outside"

    @property
    def state(self):
        """Return the total number of occupants in outdoor areas."""
        return sum(
            self._occupancy_tracker.get_occupancy(area)
            for area, config in self._occupancy_tracker.config["areas"].items()
            if not config.get("indoors", True)
        )


class TotalOccupantsSensor(SensorEntity):
    """Sensor for total number of occupants in the system."""

    def __init__(self, occupancy_tracker: OccupancyTracker):
        self._occupancy_tracker = occupancy_tracker
        self._attr_name = "Total Occupants"
        self._attr_unique_id = "total_occupants"

    @property
    def state(self):
        """Return the total number of occupants in all areas."""
        return sum(
            self._occupancy_tracker.get_occupancy(area)
            for area in self._occupancy_tracker.config["areas"]
        )
