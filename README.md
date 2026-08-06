# Occupancy Tracker [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

A conservative Home Assistant integration for room occupancy tracking with unreliable motion sensors.

## Features

- **Pessimistic Indoor Occupancy**: Positive evidence is retained through sleep, sensor OFF gaps, and ambiguous movement
- **Multi-Area Support**: Every configured area of an active motion/person sensor is marked occupied
- **Room-Aware Freshness**: Fast diagnostics for corridors, ordinary decay for living areas, and sleep-tolerant decay for bedrooms
- **Short-Lived Activity**: A separate room-aware signal for recent motion or contact activity
- **Multiple Occupants**: Movement by one person cannot clear a room where another may remain
- **Live Outdoor State**: Outdoor occupancy follows current trusted sensor evidence
- **Flexible Sensors**: Motion, magnetic (door/window), and camera detection
- **Anomaly Detection**: Alerts for stuck sensors and unusual patterns
- **Explicit Cleanup**: Clear all stale latches with a button or one room with a service
- **Restart Persistence**: Quiet occupied rooms survive Home Assistant restarts
- **Structured Audit Trail**: Every accepted or ignored sensor decision, clear, restore, trust change, and warning lifecycle is recorded as JSONL

## Quick Start

Add to your `configuration.yaml`:

```yaml
occupancy_tracker:
  areas:
    living_room:
      name: "Living Room"
      profile: living
    hallway:
      name: "Hallway"
      profile: transition
    kitchen:
      name: "Kitchen"
    bedroom:
      name: "Bedroom"
      profile: sleeping
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
    profile: living      # Optional: transition, living, or sleeping
```

Indoor areas remain conservative even if they are marked `exit_capable`.
Outdoor areas are not latched and return vacant when trusted live evidence ends.

The room profile changes only convenience signals and diagnostics:

| Profile | Intended space | Activity hold | Freshness and stale diagnostics |
| --- | --- | ---: | --- |
| `transition` | Corridor, hall, stairs | 20 seconds | Fast |
| `living` | Kitchen, lounge, office | 2 minutes | Normal |
| `sleeping` | Bedroom | 15 minutes | Sleep-tolerant |

`living` is the default. Existing `transition: true` configuration maps to the
`transition` profile for compatibility. No profile, timeout, warning, or
freshness value can automatically clear durable indoor occupancy.

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

For each configured area, the integration creates two binary sensors:

- `binary_sensor.<area>_occupancy` is the durable safety signal. It answers
  whether someone could still be present.
- `binary_sensor.<area>_activity` is ON while a trusted configured motion sensor
  is ON or for the room profile's hold time after motion or a contact edge. It
  may turn OFF while someone is sitting still or sleeping and never clears
  occupancy.

Occupancy attributes include the boolean-compatible count, `evidence_state`
(`unknown`, `active`, `stale`, `inferred`, or `vacant`), active sensor IDs, freshness,
last positive evidence, stale time, and explicit clear reason. `probability`
remains as a compatibility alias for `freshness`.

If no durable state exists after startup, indoor occupancy entities remain
unavailable with `evidence_state: unknown`; missing storage is not reported as
confident vacancy. Door/window contacts update contact activity without being
misrepresented as PIR motion or latching occupancy.

System-wide entities:

- `sensor.detected_anomalies` - Active anomaly count and details
- `button.reset_anomalies` - Clear anomaly state
- `button.clear_stale_occupancy` - Explicitly clear all stale indoor occupancy

To clear only one known-stale room without affecting any other room:

```yaml
action: occupancy_tracker.clear_stale_occupancy
data:
  area_id: living_room
```

The service refuses to clear a room while one of its trusted configured
sensors is currently ON.

## How It Works

The system uses a conservative event-driven state machine:

1. **Motion detected (ON)** → Marks area occupied immediately. Checks adjacent rooms for a "plausible source" (occupancy or active motion) and flags anomalies if none found.
2. **Motion cleared (OFF)** → Updates freshness but does not claim the indoor room is vacant.
3. **Movement elsewhere** → Updates diagnostics without clearing previously possible indoor occupancy.
4. **Freshness decay** → The score drops at the room profile's rate, but cannot clear indoor occupancy.
5. **Activity timeout** → The separate activity entity turns OFF after its room profile's quiet period; occupancy is unchanged.
6. **Explicit cleanup** → Clears all stale latches with the button or one stale room with the service; currently active sensors remain occupied.
7. **Anomaly alerts** → Flags unexpected movement, stuck sensors, extended occupancy, and stale-looking state without changing occupancy.

The indoor latch is stored in Home Assistant's versioned storage. It is restored before current sensor baselines, so an initial PIR OFF state cannot erase a quiet occupied room.

The integration writes three rotating files in the Home Assistant configuration
directory:

- `occupancy_tracker.log` contains concise operational changes and warnings at INFO level.
- `occupancy_tracker_audit.jsonl` is the authoritative structured trail, including source and receipt timestamps, accepted or ignored decisions, restore/clear actions, sensor trust changes, and warning resolution.
- `occupancy_tracker_raw.csv` remains as a sensor-only compatibility export for older replay tools.

The in-memory history verifier is non-mutating. Its bounded history is useful for
checking determinism, but is never treated as authority to erase persistent
occupancy.

Late events cannot rewind a newer state for the same sensor. Invalid,
out-of-order, and duplicate events remain visible in the audit with an explicit
decision instead of silently changing occupancy.

Do not use the durable occupancy entity as an automatic lights-off signal: by
design it may remain ON after motion stops. Use the room's raw motion sensors
or the activity entity for convenience automation; use occupancy for the
safety question "could someone still be here?"

For technical details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Troubleshooting

**Lights not turning on?**
- Check for "Unexpected Motion" warnings in `sensor.detected_anomalies`
- Verify the area is in your adjacency map
- Ensure adjacent areas have sensors

**Occupancy stuck?**
- Look for stuck sensor warnings
- Use the per-room service when only one latch is known to be stale
- Use **Clear Stale Occupancy** when all conservative state is no longer useful

**Erratic behavior?**
- Review your adjacency map (are all connections defined?)
- Check sensor entity IDs match your configuration
- Inspect `occupancy_tracker_audit.jsonl` for the accepted or ignored decision

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
