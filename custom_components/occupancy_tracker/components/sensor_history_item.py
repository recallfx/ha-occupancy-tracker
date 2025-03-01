class SensorHistoryItem:
    """Class representing a history item for a sensor."""
    
    def __init__(self, state: bool, timestamp: float):
        self.state = state
        self.timestamp = timestamp
