# Occupancy Tracker [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

A Home Assistant integration for room occupancy tracking with unreliable motion
sensors. A room is released only by evidence of leaving, or by a policy ceiling
that bounds the doubt.

[BEHAVIOR.md](BEHAVIOR.md) is the binding specification of how the integration
behaves. Read it before changing anything.

## Features

- **Exit-Gated Occupancy**: When a room's sensors fall silent it is held, then released if an exit or its own door contact fired as the person could have left, otherwise retained until its own motion returns or the profile's ceiling expires
- **Multi-Area Support**: Every configured area of an active motion/person sensor is marked occupied
- **Room Profiles**: Corridors clear in 30 seconds; ordinary rooms retain up to 2 hours, living areas 4 hours, bedrooms 12 hours
- **Short-Lived Activity**: A separate room-aware signal for recent motion or contact activity
- **Multiple Occupants**: Neighbor motion alone never clears a room
- **Live Outdoor State**: Outdoor occupancy follows current trusted sensor evidence
- **Flexible Sensors**: Motion, magnetic (door/window), and camera detection
- **Unavailable Is Not Vacant**: A room whose motion inputs all go unavailable publishes `unavailable`, not `off`
- **Anomaly Detection**: Alerts for stuck sensors and unusual patterns
- **Explicit Cleanup**: Clear every held room with a button or one room with a service
- **Restart Persistence**: Held rooms survive Home Assistant restarts without being granted a fresh hold
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
      exit_capable: true  # Diagnostics only; see BEHAVIOR.md section 4
  
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
    exit_capable: false  # Diagnostics only; see BEHAVIOR.md section 4
    profile: living      # Optional: transition, default, living, or sleeping
```

`exit_capable` affects diagnostics only (see BEHAVIOR.md section 4). Set
`indoors: false` for yards and porches; outdoor areas mirror their sensors and
are never exits.

The room profile carries the timing policy of the state machine, plus the
activity, freshness, and warning tuning:

| Profile | Intended space | Hold after own motion stops | Retention ceiling | Activity hold |
| --- | --- | ---: | ---: | ---: |
| `transition` | Corridor, hall, stairs | 30 seconds | never retains | 20 seconds |
| `default` | Bathroom, utility, garage, entrance | 90 seconds | 2 hours | 2 minutes |
| `living` | Kitchen, lounge, office | 90 seconds | 4 hours | 2 minutes |
| `sleeping` | Bedroom | 90 seconds | 12 hours | 15 minutes |

`default` is the profile when none is set. Existing `transition: true`
configuration maps to the `transition` profile for compatibility.

### Adjacency Map

Define which areas are physically connected:

```yaml
adjacency:
  living_room: [kitchen, hallway]
  kitchen: [living_room, dining_room]
  hallway: [living_room, bedroom]
```

The system automatically makes connections bidirectional. Adjacency also
defines each room's exits: the indoor neighbors through which somebody can
leave. A neighbor whose only indoor connection is the room itself is a dead
end, such as an ensuite or a walk-in wardrobe, so walking into it is not
leaving.

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

- `binary_sensor.<slugified area name>_occupancy` answers whether someone is in
  the room. It is ON while the room is occupied, held, or retained.
- `binary_sensor.<slugified area name>_activity` is ON while a trusted
  configured motion sensor is ON or for the room profile's activity hold (the
  `Activity hold` column above) after motion or a contact edge. It may turn OFF
  while someone is sitting still or sleeping and never feeds the occupancy
  decision.

The object id comes from the area's `name:`, slugified by Home Assistant, and
falls back to the area id when `name:` is absent. An area id of `guest_room`
with `name: "Guest Toilet"` publishes
`binary_sensor.guest_toilet_occupancy`.

Occupancy attributes explain the decision: `state`
(`vacant`, `occupied`, `pending`, `retained`, or `unknown`), `reason`,
`deadline`, `confirmed`, `evidence_age`, `exits`, `room_profile`, active sensor
IDs, freshness, last positive evidence, stale time, and explicit clear reason.
`probability` remains as a compatibility alias for `freshness`.

A room whose motion inputs have all been unavailable for 60 seconds publishes
`unavailable` rather than a confident `off`. Door/window contacts are departure
evidence; they never fabricate motion.

System-wide entities:

- `sensor.detected_anomalies` - Active anomaly count and details
- `button.reset_anomalies` - Clear anomaly state
- `sensor.total_occupants`, `sensor.total_occupants_inside`, `sensor.total_occupants_outside` - Counts of occupied areas (a room whose occupancy entity is unavailable still counts)
- `sensor.occupied_inside_areas`, `sensor.occupied_outside_areas` - Counts with the area list as an attribute (a room whose occupancy entity is unavailable still counts)
- `button.clear_stale_occupancy` - Explicitly clear every held indoor room

To clear only one known-stale room without affecting any other room:

```yaml
action: occupancy_tracker.clear_stale_occupancy
data:
  area_id: living_room
```

The service refuses to clear a room while one of its motion sensors is
currently trusted ON (an open door or window contact does not block a clear).

## How It Works

Each room runs an exit-gated state machine. The full rules, with diagrams and
worked timelines, are in [BEHAVIOR.md](BEHAVIOR.md); the outline:

1. **Motion detected (ON)** → The room becomes occupied at once, canceling any hold. An activation that starts within 2 seconds of an exit neighbor's activation is detector spill: it does not confirm an entry, and a room already held ignores it.
2. **Motion cleared (OFF)** → The room is held for the profile's hold time, not declared vacant.
3. **At the hold deadline** → Vacant if an exit neighbor, or the room's own door contact, fired within 30 seconds of the room going quiet (a departure trail); vacant if the entry was never confirmed, or the profile never retains; otherwise retained.
4. **Retained** → Released by the room's own motion, or by the profile's retention ceiling.
5. **Movement elsewhere** → Never changes a room on its own.
6. **Freshness decay and activity timeout** → Diagnostics and the activity entity only; occupancy is unchanged.
7. **Explicit cleanup** → Clears held rooms with the button or one room with the service; rooms with currently active motion are refused.
8. **Anomaly alerts** → Flag unexpected movement, stuck sensors, extended occupancy, and stale-looking state without changing occupancy. A sensor ON for 24 hours is the one exception: it stops being trusted.

The decision state is stored in Home Assistant's versioned storage and restored
before current sensor baselines. A restart never grants a fresh hold.

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

Do not use the occupancy entity as an automatic lights-off signal: by design it
may remain ON after motion stops. Use the room's raw motion sensors or the
activity entity for convenience automation.

For the behavior rules see [BEHAVIOR.md](BEHAVIOR.md); for the code layout see
[ARCHITECTURE.md](ARCHITECTURE.md).

## Troubleshooting

**Lights not turning on?**
- Check that the room's motion sensor is available and trusted: the occupancy entity's `active_sensors` attribute lists the sensors currently providing live evidence
- Check the `state` and `reason` attributes of `binary_sensor.<area>_occupancy`
- See BEHAVIOR.md section 12 for the accepted error cases

**Occupancy stuck?**
- Look for stuck sensor warnings
- Check the entity's `state`, `reason`, and `deadline` attributes: a `retained` room is waiting for its own motion or its ceiling
- Use the per-room service when one room is known to be wrong, and **Clear Stale Occupancy** to clear every held room. Needing either is a bug report, not a workaround

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

See [BEHAVIOR.md](BEHAVIOR.md) for the rules the tests pin, and
[ARCHITECTURE.md](ARCHITECTURE.md) for the code layout.

## Contributing

Contributions welcome! Please:
1. Open an issue to discuss major changes
2. Follow existing code style (ruff formatting)
3. Add tests for new features
4. Change [BEHAVIOR.md](BEHAVIOR.md) before changing behavior, and update the rest of the documentation as needed

## License

MIT License - see [LICENSE](LICENSE) file

## Credits

Built for the Home Assistant community
