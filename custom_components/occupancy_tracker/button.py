"""Button platform for resetting anomaly detection."""

from homeassistant.components.button import ButtonEntity
from . import DOMAIN


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Occupancy Tracker button platform."""
    occupancy_tracker = hass.data[DOMAIN]["occupancy_tracker"]
    async_add_entities([ResetAnomaliesButton(occupancy_tracker)], True)


class ResetAnomaliesButton(ButtonEntity):
    """Button entity to reset anomaly detection."""

    def __init__(self, occupancy_tracker):
        self._occupancy_tracker = occupancy_tracker
        self._attr_name = "Reset Anomalies"
        self._attr_unique_id = "reset_anomalies_button"

    async def async_press(self) -> None:
        """Handle the button press - reset anomalies."""
        self._occupancy_tracker.reset_anomalies()
