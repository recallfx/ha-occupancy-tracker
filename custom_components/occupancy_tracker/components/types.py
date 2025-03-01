from typing import Dict, List, Literal, TypedDict, Union


class AreaConfig(TypedDict, total=False):
    name: str
    indoors: bool
    exit_capable: bool


class MotionSensorConfig(TypedDict):
    area: str
    type: Literal["motion", "camera_motion", "camera_person"]


class MagneticSensorConfig(TypedDict):
    between_areas: List[str]
    type: Literal["magnetic"]


SensorConfig = Union[MotionSensorConfig, MagneticSensorConfig]


class OccupancyTrackerConfig(TypedDict):
    areas: Dict[str, AreaConfig]
    adjacency: Dict[str, List[str]]
    sensors: Dict[str, SensorConfig]
