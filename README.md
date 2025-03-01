# Occupancy Tracker [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

A sophisticated occupancy tracking integration that combines probabilistic state machine tracking with anomaly detection for reliable room presence detection.

## Core Features

- **Multi-Area Tracking**: 
  - Track occupancy across interconnected areas
  - Support for partial transitions and chain movements
  - Configurable area connections through adjacency maps
  - Special handling for exit-capable areas (e.g., frontyard, backyard)

- **Robust Occupancy Detection**:
  - Probabilistic occupancy scoring (0.05 to 0.95 confidence)
  - Handles simultaneous multi-occupant scenarios
  - Motion clearing time considerations (5s default threshold)
  - Preserves occupancy during no-motion periods (e.g., sleep scenarios)

- **Flexible Sensor Support**:
  - Motion sensors in rooms
  - Magnetic sensors for doors/windows
  - External camera motion detection
  - Multi-area bridging sensors (e.g., doorways between rooms)

- **Built-in Anomaly Detection**:
  - Long sensor activations (customizable threshold, default 300s)
  - Impossible occupant appearances
  - Suspicious transitions without adjacent presence
  - Multi-occupant movement anomalies

## Configuration

The system requires a YAML configuration file defining:

```yaml
areas:
  room_id:
    name: "Room Name"
    exit_capable: true/false  # For areas like frontyard

adjacency:
  room_id: [adjacent_room_ids]

sensors:
  sensor_id:
    area: room_id | [room_id1, room_id2]  # Single area or bridging
    type: motion | magnetic | camera_motion | camera_person
```

## Installation (with HACS)

1. Add repository in HACS:
   * **Repository**: `recallfx/occupancy_tracker`
   * **Category**: Integration

2. Install and restart Home Assistant

## Installation (manual)

1. Download latest release
2. Copy to `custom_components/occupancy_tracker`
3. Restart Home Assistant

## Usage Example

```yaml
areas:
  main_bathroom:
    name: "Main Bathroom"
  main_bedroom: 
    name: "Main Bedroom"
  frontyard:
    name: "Front Yard"
    exit_capable: true

adjacency:
  main_bathroom: [main_bedroom]
  main_bedroom: [main_bathroom]
  frontyard: []

sensors:
  motion_main_bathroom:
    area: main_bathroom
    type: motion
  motion_main_bedroom:
    area: main_bedroom
    type: motion
  front_door_magnetic:
    area: [frontyard, entrance]
    type: magnetic
```

## Detailed Functionality

### Occupancy Logic
- Single occupant transitions between rooms are tracked with 95% confidence when confirmed
- Multiple occupant tracking with preserved counts (e.g., if 2 occupants in bathroom, 1 moves to bedroom)
- Chain movement tracking (e.g., bathroom → bedroom → hall) with probability degradation
- Motion clearing reduces confidence but doesn't remove occupancy (75% confidence after clearing)
- Exit-capable areas (like frontyard) automatically clear occupancy when motion stops

### Sensor Behaviors
- Motion sensors: Primary room presence detection
- Magnetic sensors: Handles bi-directional transitions between areas
- Multi-area sensors: Support partial occupancy distribution
- Default thresholds:
  - Short transitions: 5 seconds (configurable)
  - Long detections: 300 seconds (configurable)

### Anomaly Detection
- Long sensor activations (>300s) flagged as potential stuck sensors
- Impossible appearances detected when occupancy appears without adjacent presence
- Exit-capable areas excluded from impossible appearance detection
- Transitions validated against adjacency map

## Development

### Requirements
- Python 3.13+
- PyYAML for configuration handling
- `uv` for package management and environment
- `ruff` for code linting

### Testing
The project includes comprehensive test suites:
- `test_occupancy_tracker.py`: Core movement and transition logic
- `test_anomaly_detector.py`: Anomaly detection validation
- `test_occupancy_tracker.py`: Integrated system behavior
- `test_config_validator.py`: Configuration validation

### Configuration Validation
The system validates:
- All areas are properly defined in adjacency maps
- Each area has at least one sensor
- Sensor areas match defined areas
- Multi-area sensors bridge valid adjacent spaces

## Credits

Inspired by Home Assistant community