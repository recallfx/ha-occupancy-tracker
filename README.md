# Occupancy Tracker [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

A conservative Home Assistant integration for room occupancy tracking with unreliable motion sensors.

## Features

- **Pessimistic Indoor Occupancy**: Positive evidence is retained through sleep, sensor OFF gaps, and ambiguous movement
- **Multi-Area Support**: Every configured area of an active motion/person sensor is marked occupied
- **Freshness Score**: Time-since-motion scoring for diagnostics, never for indoor clearing
- **Multiple Occupants**: Movement by one person cannot clear a room where another may remain
- **Live Outdoor State**: Outdoor occupancy follows current trusted sensor evidence
- **Flexible Sensors**: Motion, magnetic (door/window), and camera detection
- **Anomaly Detection**: Alerts for stuck sensors and unusual patterns
- **Explicit Cleanup**: A button clears stale indoor latches while preserving active sensor evidence
- **Restart Persistence**: Quiet occupied rooms survive Home Assistant restarts

## Quick Start

Add to your `configuration.yaml`:

```yaml
occupancy_tracker:
  areas:
    living_room:
      name: "Living Room"
    kitchen:
      name: "Kitchen"
    front_door:
      name: "Front Door"
      exit_capable: true  # People can leave the system from here
  
  adjacency:
    living_room: [kitchen, front_door]
    kitchen: [living_room]
    front_door: [living_room]
  
  sensors:
    binary_sensor.living_room_motion:
      area: living_room
      type: motion
    binary_sensor.kitchen_motion:
      area: kitchen
      type: motion
    binary_sensor.front_door:
      area: [front_door, living_room]  # Bridging sensor
      type: magnetic
```

## Installation

### Via HACS (Recommended)

1. Open HACS → Integrations
2. Click "Explore & Download Repositories"
3. Search for "Occupancy Tracker"
4. Download and restart Home Assistant
5. Add configuration to `configuration.yaml` (see Quick Start)

### Manual Installation

1. Download the [latest release](https://github.com/recallfx/occupancy_tracker/releases)
2. Extract to `custom_components/occupancy_tracker`
3. Restart Home Assistant
4. Add configuration to `configuration.yaml`

## Configuration Reference

### Areas

Define all rooms/spaces you want to track:

```yaml
areas:
  area_id:
    name: "Display Name"
    exit_capable: false  # Optional: set true for entry/exit points
```

Indoor areas remain conservative even if they are marked `exit_capable`.
Outdoor areas are not latched and return vacant when trusted live evidence ends.

### Adjacency Map

Define which areas are physically connected:

```yaml
adjacency:
  living_room: [kitchen, hallway]
  kitchen: [living_room, dining_room]
  hallway: [living_room, bedroom]
```

The system automatically makes connections bidirectional.

### Sensors

Map your Home Assistant sensors to areas:

```yaml
sensors:
  binary_sensor.living_room_motion:
    area: living_room
    type: motion
  
  binary_sensor.front_door:
    area: [entryway, front_porch]  # Bridging sensor
    type: magnetic
```

**Supported sensor types:**
- `motion`: Standard motion sensors
- `magnetic`: Door/window contacts
- `camera_motion`: Camera motion detection
- `camera_person`: Camera person detection

## Entities Created

For each configured area, the integration creates an occupancy binary sensor.
Its attributes include the boolean-compatible occupancy count, `evidence_state`
(`active`, `stale`, `inferred`, or `vacant`), active sensor IDs, freshness,
last positive evidence, stale time, and explicit clear reason. `probability`
remains as a compatibility alias for `freshness`.

System-wide entities:

- `sensor.detected_anomalies` - Active anomaly count and details
- `button.reset_anomalies` - Clear anomaly state
- `button.clear_stale_occupancy` - Explicitly clear stale indoor occupancy

## How It Works

The system uses a conservative event-driven state machine:

1. **Motion detected (ON)** → Marks area occupied immediately. Checks adjacent rooms for a "plausible source" (occupancy or active motion) and flags anomalies if none found.
2. **Motion cleared (OFF)** → Updates freshness but does not claim the indoor room is vacant.
3. **Movement elsewhere** → Updates diagnostics without clearing previously possible indoor occupancy.
4. **Freshness decay** → The score drops over time, but cannot clear indoor occupancy.
5. **Explicit cleanup** → Clears only stale indoor latches; currently active sensors remain occupied.
6. **Anomaly alerts** → Flags unexpected movement, stuck sensors, extended occupancy, and stale-looking state without changing occupancy.

The indoor latch is stored in Home Assistant's versioned storage. It is restored before current sensor baselines, so an initial PIR OFF state cannot erase a quiet occupied room.

Do not use the durable occupancy entity as an automatic lights-off signal: by
design it may remain ON after motion stops. Use the room's raw motion sensors
for lighting timeouts; use occupancy for the safety question "could someone
still be here?"

For technical details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Troubleshooting

**Lights not turning on?**
- Check for "Unexpected Motion" warnings in `sensor.occupancy_tracker_warnings`
- Verify the area is in your adjacency map
- Ensure adjacent areas have sensors

**Occupancy stuck?**
- Look for stuck sensor warnings
- Use **Clear Stale Occupancy** when conservative state is no longer useful

**Erratic behavior?**
- Review your adjacency map (are all connections defined?)
- Check sensor entity IDs match your configuration
- Enable debug logging: `logger: custom_components.occupancy_tracker: debug`

## Development

### Setup

```bash
# Clone repository
git clone https://github.com/recallfx/occupancy_tracker.git
cd occupancy_tracker

# Install dependencies (requires uv)
uv sync
```

### Running Tests

```bash
# All tests
uv run pytest tests/ -v

# Unit tests only
uv run pytest tests/occupancy_tracker/ -v

# Integration tests only
uv run pytest tests/integration/ -v

# With coverage
uv run pytest --cov=custom_components.occupancy_tracker
```

See [tests/integration/README.md](tests/integration/README.md) and [ARCHITECTURE.md](ARCHITECTURE.md) for more details.

## Contributing

Contributions welcome! Please:
1. Open an issue to discuss major changes
2. Follow existing code style (ruff formatting)
3. Add tests for new features
4. Update documentation as needed

## License

MIT License - see [LICENSE](LICENSE) file

## Credits

Built for the Home Assistant community
