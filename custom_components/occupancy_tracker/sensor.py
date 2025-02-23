from homeassistant.components.sensor import SensorEntity

from . import DOMAIN


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Occupancy Tracker sensors."""
    occupancy_system = hass.data[DOMAIN]["occupancy_system"]
    sensors = []

    # Individual area sensors
    for area in occupancy_system.config["areas"]:
        sensors.append(OccupancyCountSensor(occupancy_system, area))
        sensors.append(OccupancyProbabilitySensor(occupancy_system, area))

    # Global sensors
    sensors.extend([
        OccupiedInsideAreasSensor(occupancy_system),
        OccupiedOutsideAreasSensor(occupancy_system),
        TotalOccupantsInsideSensor(occupancy_system),
        TotalOccupantsOutsideSensor(occupancy_system),
        TotalOccupantsSensor(occupancy_system),
        AnomalySensor(occupancy_system)
    ])

    async_add_entities(sensors, True)


class OccupancyCountSensor(SensorEntity):
    """Sensor for occupancy count."""

    def __init__(self, occupancy_system, area):
        self._occupancy_system = occupancy_system
        self._area = area
        self._attr_name = f"Occupancy Count {area}"
        self._attr_unique_id = f"occupancy_count_{area}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._occupancy_system.get_occupancy(self._area)


class OccupancyProbabilitySensor(SensorEntity):
    """Sensor for occupancy probability."""

    def __init__(self, occupancy_system, area):
        self._occupancy_system = occupancy_system
        self._area = area
        self._attr_name = f"Occupancy Probability {area}"
        self._attr_unique_id = f"occupancy_probability_{area}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._occupancy_system.get_occupancy_probability(self._area)


class AnomalySensor(SensorEntity):
    """Sensor for detected anomalies."""

    def __init__(self, occupancy_system):
        self._occupancy_system = occupancy_system
        self._attr_name = "Detected Anomalies"
        self._attr_unique_id = "detected_anomalies"

    @property
    def state(self):
        """Return the state of the sensor."""
        return len(self._occupancy_system.get_anomalies())

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {"anomalies": self._occupancy_system.get_anomalies()}


class OccupiedInsideAreasSensor(SensorEntity):
    """Sensor that lists all occupied indoor areas."""

    def __init__(self, occupancy_system):
        self._occupancy_system = occupancy_system
        self._attr_name = "Occupied Inside Areas"
        self._attr_unique_id = "occupied_inside_areas"

    @property
    def state(self):
        """Return the number of occupied indoor areas."""
        occupied_areas = [
            area for area, config in self._occupancy_system.config["areas"].items()
            if config.get("indoors", True) and self._occupancy_system.get_occupancy(area) > 0
        ]
        return len(occupied_areas)

    @property
    def extra_state_attributes(self):
        """Return the list of occupied indoor areas."""
        return {
            "areas": [
                area for area, config in self._occupancy_system.config["areas"].items()
                if config.get("indoors", True) and self._occupancy_system.get_occupancy(area) > 0
            ]
        }


class OccupiedOutsideAreasSensor(SensorEntity):
    """Sensor that lists all occupied outdoor areas."""

    def __init__(self, occupancy_system):
        self._occupancy_system = occupancy_system
        self._attr_name = "Occupied Outside Areas"
        self._attr_unique_id = "occupied_outside_areas"

    @property
    def state(self):
        """Return the number of occupied outdoor areas."""
        occupied_areas = [
            area for area, config in self._occupancy_system.config["areas"].items()
            if not config.get("indoors", True) and self._occupancy_system.get_occupancy(area) > 0
        ]
        return len(occupied_areas)

    @property
    def extra_state_attributes(self):
        """Return the list of occupied outdoor areas."""
        return {
            "areas": [
                area for area, config in self._occupancy_system.config["areas"].items()
                if not config.get("indoors", True) and self._occupancy_system.get_occupancy(area) > 0
            ]
        }


class TotalOccupantsInsideSensor(SensorEntity):
    """Sensor for total number of occupants inside."""

    def __init__(self, occupancy_system):
        self._occupancy_system = occupancy_system
        self._attr_name = "Total Occupants Inside"
        self._attr_unique_id = "total_occupants_inside"

    @property
    def state(self):
        """Return the total number of occupants in indoor areas."""
        return sum(
            self._occupancy_system.get_occupancy(area)
            for area, config in self._occupancy_system.config["areas"].items()
            if config.get("indoors", True)
        )


class TotalOccupantsOutsideSensor(SensorEntity):
    """Sensor for total number of occupants outside."""

    def __init__(self, occupancy_system):
        self._occupancy_system = occupancy_system
        self._attr_name = "Total Occupants Outside"
        self._attr_unique_id = "total_occupants_outside"

    @property
    def state(self):
        """Return the total number of occupants in outdoor areas."""
        return sum(
            self._occupancy_system.get_occupancy(area)
            for area, config in self._occupancy_system.config["areas"].items()
            if not config.get("indoors", True)
        )


class TotalOccupantsSensor(SensorEntity):
    """Sensor for total number of occupants in the system."""

    def __init__(self, occupancy_system):
        self._occupancy_system = occupancy_system
        self._attr_name = "Total Occupants"
        self._attr_unique_id = "total_occupants"

    @property
    def state(self):
        """Return the total number of occupants in all areas."""
        return sum(
            self._occupancy_system.get_occupancy(area)
            for area in self._occupancy_system.config["areas"]
        )
