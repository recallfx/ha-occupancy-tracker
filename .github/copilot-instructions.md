# AI Agent Instructions

> **READ THIS FIRST**: This document contains critical context for working on this codebase. Review relevant sections before making changes. Update the "Session Log" section when you make significant decisions or discoveries.

## Architecture overview

Home Assistant integration for room occupancy and short-lived activity tracking.

**Core flow**: HA sensor events → `__init__.py:state_change_listener` → `coordinator.process_sensor_event()` → `MapOccupancyResolver.process_snapshot()` → `OccupancyEngine.apply()` → `AreaState` updated → `async_set_updated_data()`. A ten-second tick (`coordinator.check_timeouts()`) advances the engine with unchanged evidence so deadlines retire on silence.

**Key components**:
- `OccupancyCoordinator` (`coordinator.py`): Owns all state (`self.areas`, `self.sensors`), orchestrates helpers, persists the engine snapshot.
- `OccupancyEngine` (`helpers/occupancy_engine.py`): The decision system. A pure per-room state machine (`vacant`, `occupied`, `pending`, `retained`, plus an `unknown` overlay) driven by an injected clock and explicit deadlines. No Home Assistant imports; the same event sequence always produces the same states.
- `MapOccupancyResolver` (`helpers/map_occupancy_resolver.py`): Thin adapter. Computes the trusted-active and all-unavailable area sets from `SensorState` and writes the engine's answer onto `AreaState`.
- `AreaState` / `SensorState` (`helpers/`): Boolean occupancy, evidence timestamps, availability, reliability, and activity history.
- `AnomalyDetector` (`helpers/anomaly_detector.py`): Generates warnings for stuck sensors, impossible movements, and timeouts. Diagnostics only, except that a sensor ON for 24 h is marked unreliable.
- `MapStateRecorder`: Captures immutable snapshots for history replay.

**Key principle**: A room is released only by evidence of leaving. Own motion occupies it; when its inputs fall silent it is held for `hold_seconds`; at that deadline it goes vacant if an exit neighbour, or its own door contact, fired after the room's last activation (a departure trail), otherwise it is retained until own motion or the profile's retention ceiling. Neighbour motion alone never clears a room. Adjacency defines exits; a neighbour whose only indoor connection is the room itself is a dead end, not an exit. The design and its evidence are in `output/occupancy-handoff-2026-09-06.md` in the parent workspace.

## Configuration

YAML-only via `async_setup` in `__init__.py`. **No Config Flow**.

```yaml
occupancy_tracker:
  areas:
    living_room:
      name: "Living Room"
      exit_capable: false  # informational
      indoors: true        # false for yards, porches
      profile: living      # transition, default, living, sleeping
  adjacency:
    living_room: [kitchen, hallway]  # auto-bidirectional; defines exits
  sensors:
    binary_sensor.living_room_motion:
      area: living_room  # or [area1, area2] for bridging sensors
      type: motion  # motion, magnetic, camera_motion, camera_person
```

Schema validated with voluptuous. Coordinator stored at `hass.data[DOMAIN]["coordinator"]`.

## Testing

```bash
uv run pytest tests/                    # all tests
uv run pytest tests/occupancy_tracker/  # unit tests
uv run pytest tests/integration/        # integration tests
uv run pytest -v -m end_to_end          # by marker
```

**Test organization**: Mirrors source structure. Engine contract tests in `tests/occupancy_tracker/helpers/test_occupancy_engine.py` drive the engine with a virtual clock. Integration tests in `tests/integration/` drive the coordinator directly (`conftest.py` helpers) or go through Home Assistant with `async_setup_component`, real state changes, and `freezer` (`test_home_assistant_level.py`). `test_ambiguity_worlds.py` checks the engine against the non-identifiable cases from the data analysis.

**Unit test pattern**: Mock `HomeAssistant`, pass config dict directly to `OccupancyCoordinator(hass, config)`.

CI also runs the full suite against Home Assistant 2026.8.3/Python 3.14, matching the deployed host.

## Critical patterns

**Motion-ON**: The room enters `occupied` and any pending or retained hold is cancelled. An activation that starts within 2 s of an exit neighbour's activation while that neighbour is ON is detector spill: a vacant room enters unconfirmed (hold only, never retained); a held room ignores it.

**Motion-OFF**: The room enters `pending` with `deadline = OFF + hold_seconds` (90 s; 30 s for transition rooms). The tick retires the deadline: departure trail → `vacant`; no trail and confirmed → `retained` until own motion or the retention ceiling (sleeping 12 h, living 4 h, default 2 h, transition 0).

**Room profiles**: Carry `hold_seconds` and `retention_ceiling_seconds` plus the activity, freshness, and warning tuning. `transition: true` selects the `transition` profile unless `profile:` is set.

**Contacts**: A pulse on a room's own door or window contact is departure evidence, timed like an exit activation. It never fabricates motion.

**Unavailable**: Not OFF. A room whose motion inputs are all unavailable for 60 s publishes `unknown` and its entity becomes `unavailable`. Deadlines are frozen meanwhile; a sensor that returns still ON is a continuation, not a new edge.

**Persistence**: Storage version 2, `{"initialized": true, "rooms": {area: RoomRuntime}}`, indoor rooms only. Version 1 latches are discarded on migration. Restore re-evaluates stored deadlines against now and never grants a fresh hold.

**Audit**: `occupancy_tracker_audit.jsonl` records source/receipt times, accepted and ignored decisions, room transitions, clears, restores, trust changes, and warning lifecycle.

## Simulation

Interactive web UI for testing: `python -m simulation.server` then open http://localhost:8080

Uses `SimOccupancyCoordinator` wrapping the real coordinator, loads from `config.yaml`.

## Common pitfalls

- The ten-second tick is load-bearing: it is the only path that retires holds and ceilings. It publishes entities only on a change, else once a minute, because the entities carry time-derived attributes and every publish is a recorder row per entity.
- Adjacency is auto-bidirectional and defines exits.
- `exit_capable` is informational. Outdoor areas mirror their sensors and are never exits.
- `indoors` defaults to true; set false for outdoor areas.
- Sensor entity IDs must match HA format (`binary_sensor.xyz`).
- State is mutable - `MapOccupancyResolver` modifies `AreaState` objects directly.
- Rolling back to a build with storage version 1 requires deleting `.storage/occupancy_tracker.occupancy_state` first; the old code cannot load a newer store.

## Writing style

Write like a human. Avoid flowery language, summary phrases, vague statements, and common AI patterns. Be direct and specific.

---

## Session Log

Document significant decisions, findings, and context that future sessions need to know. Most recent entries first.

### 2026-09-07: Exit-gated state machine replaces the latch
- `OccupancyEngine` decides occupancy per room with explicit deadlines; the permanent indoor latch is gone. Storage version 2 discards the old latches.
- A spill-timed re-trigger of a held room is ignored, and an availability recovery is a continuation, so neither can erase a departure trail or re-anchor a ceiling.
- Known open item: a departure trail is not spill-qualified (see `_note_exit_edge`). Fixing it needs a threshold of its own; the replay numbers are in the parent workspace's `output/`.
- `binary_sensor.workshop_magnet` (KNX 8/2/11) is the front door and is mapped to the entrance.

### 2026-08-07: Conservative profiles, persistence, and audit
- Added transition/living/sleeping profiles for activity, freshness, and diagnostic decay without weakening durable occupancy.
- Missing persistence now stays unknown; explicit clears and possible occupancy survive restart with motion/contact evidence.
- Contacts no longer fabricate motion, warning predicates resolve, stuck sensors are checked periodically, and old events cannot rewind newer sensor state.
- Added concise operational logging, structured JSONL audit, and a CI gate matching production Home Assistant 2026.7.3.

### 2025-12-20: Activation-Window Refactor
- Refactored `MapOccupancyResolver` to use an activation-window model.
- Movement now happens on **Motion-OFF** if a neighbor activated after the source turned ON.
- **Motion-ON** now only records entry and checks for plausible sources (anomalies).
- Periodic consistency checks disabled to simplify logic and improve predictability.
- Added `indoors` config for areas to better detect outdoor-to-indoor intrusions.

### 2025-12-20: Simulation reset control
- Added simulation reset command via WebSocket and UI button in the simulator header
- Reset flow clears backend areas/sensors/history and resets local draggable people/input system (exits history mode first)
- Use the "Reset State" button (requires active WS connection)

### 2024-11-26: Instructions file created
- Established architecture documentation with core flow, components, and patterns
- Key insight: `MapOccupancyResolver` is stateless and mutates `AreaState` in-place
- The old `AreaManager`/`SensorManager` classes no longer exist - coordinator owns state directly
- Motion-OFF logic is critical: person STAYS by default, only moves with explicit evidence

### Template for new entries
```
### YYYY-MM-DD: Brief title
- What was decided/discovered
- Why it matters
- Any gotchas or follow-ups
```
