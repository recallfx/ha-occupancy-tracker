from typing import Optional

class Warning:
    """Class representing a warning in the system."""
    
    def __init__(self, warning_type: str, message: str, area: Optional[str], 
                 sensor_id: Optional[str], timestamp: float):
        self.type = warning_type
        self.message = message
        self.area = area
        self.sensor_id = sensor_id
        self.timestamp = timestamp
        self.is_active = True
        self.id = f"{warning_type}_{area or ''}_{sensor_id or ''}_{self.timestamp}"
    
    def resolve(self):
        """Mark this warning as resolved."""
        self.is_active = False
    
    def __str__(self) -> str:
        return f"Warning[{self.type}]: {self.message}"
