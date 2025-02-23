from homeassistant.components.sensor import SensorEntity

from . import DOMAIN


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Occupancy Tracker sensors."""
    occupancy_system = hass.data[DOMAIN]["occupancy_system"]
    sensors = []

    for area in occupancy_system.config["areas"]:
        sensors.append(OccupancyCountSensor(occupancy_system, area))
        sensors.append(OccupancyProbabilitySensor(occupancy_system, area))

    sensors.append(AnomalySensor(occupancy_system))

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
