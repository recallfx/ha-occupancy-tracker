"""pytest fixtures."""

import pytest
from homeassistant.core import HomeAssistant

@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations defined in the test dir."""
    return
