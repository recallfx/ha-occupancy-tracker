# Occupancy Tracker Architecture

How the code is put together. **What it must do is in
[BEHAVIOR.md](BEHAVIOR.md)**, the binding specification: the state machine, the
timing constants, the fault rules, the invariants, and the differences between
the specification and the code today. Nothing in this file overrides it.

## Component map

| File | Responsibility |
| --- | --- |
| `__init__.py` | YAML schema and validation, log and audit handlers, the state-change listener, the startup baseline pass, the 10 s interval, the `clear_stale_occupancy` service. |
| `coordinator.py` | Owns `self.areas` and `self.sensors`. Ingests sensor events (validity, ordering, duplicate and keep-alive rules), runs the periodic tick, persists the engine snapshot, publishes to entities, writes the audit trail. |
| `helpers/occupancy_engine.py` | `OccupancyEngine`: the decision system. A pure per-room state machine (`vacant`, `occupied`, `pending`, `retained`, plus an `unknown` overlay) driven by an injected clock and explicit deadlines. No Home Assistant imports. Derives topology and exits from configuration. |
| `helpers/map_occupancy_resolver.py` | Thin adapter. Computes the two sets the engine needs, trusted-active areas and all-unavailable areas, and writes the engine's answer onto `AreaState`. |
| `helpers/room_profiles.py` | The named profiles and their timing numbers. |
| `helpers/area_state.py` | Per-area published state: boolean occupancy, `state_known`, evidence timestamps, activity history. |
| `helpers/sensor_state.py` | Per-sensor trust: current state, availability, reliability, stuck detection, history. |
| `helpers/anomaly_detector.py` | Read-only warnings. The one exception is marking a sensor ON for 24 h unreliable, which changes an input. |
| `helpers/map_state_recorder.py`, `helpers/history_verifier.py` | Bounded in-memory event snapshots and non-mutating determinism checks. Never authority over live state. |
| `helpers/audit.py`, `helpers/log_formatter.py` | The JSONL audit trail and the concise operational log. |
| `sensors/area_sensors.py` | The per-area occupancy and activity entities. |
| `sensors/aggregate_sensors.py`, `sensors/location_sensors.py`, `sensors/anomaly_sensor.py`, `button.py` | System-wide entities. |
| `diagnostics.py` | Assembles the coordinator payload the entities read. |

## Event flow

1. Home Assistant reports a configured sensor state change.
2. `__init__.state_change_listener` sends `unavailable`/`unknown` to
   `coordinator.invalidate_sensor_state` and any other state to
   `coordinator.process_sensor_event`, both with the source and receipt times.
3. The coordinator applies the ingestion rules (BEHAVIOR.md section 3.3),
   updates `SensorState`, and records a `MapSnapshot`.
4. `MapOccupancyResolver` computes the trusted-active and all-unavailable area
   sets and calls `OccupancyEngine.apply`. A contact ON pulse also calls
   `engine.record_contact`.
5. The engine advances every room and returns the transitions.
6. The resolver writes `occupied`, `state_known` and `stale_since` onto
   `AreaState`; the coordinator publishes, persists on a room transition, and
   audits.

A 10 s interval calls `coordinator.check_timeouts`, which runs stuck-sensor
detection, ticks the engine with unchanged evidence so deadlines retire, runs
the other diagnostics, and publishes. The tick is load-bearing: it is the only
path that releases a hold or a ceiling in a quiet house.

Only the engine changes a room's state. Everything upstream decides what
evidence it sees; everything downstream reports its answer.

## Persistence

Versioned Home Assistant storage under `occupancy_tracker.occupancy_state`,
version 2, atomic writes, indoor rooms only. Version 1 held permanent latches
and is discarded on migration. Restore runs before sensor baselines are seeded.
The rules are in BEHAVIOR.md sections 9.1 to 9.5.

## Logging and audit

Routine events without occupancy changes are DEBUG in the operational log. The
JSONL audit is authoritative: every sensor decision including ignored ones,
source and receipt timestamps, room transitions, occupancy deltas, availability
and trust changes, restores, clears, resets, and warning lifecycle. Handlers are
installed idempotently so a setup retry does not duplicate lines. The event
types are listed in BEHAVIOR.md section 11.

## Simulation

`python -m simulation.server` runs an interactive web UI on port 8123 with
`SimOccupancyCoordinator` wrapping the real coordinator and loading
`config.yaml`.

## Testing

Tests mirror the source layout. `tests/occupancy_tracker/helpers/test_occupancy_engine.py`
drives the engine with a virtual clock and is where a behavior rule is pinned.
`tests/integration/` drives the coordinator directly or goes through Home
Assistant with `async_setup_component` and a frozen clock.
`tests/occupancy_tracker/test_ambiguity_worlds.py` checks the engine against
the non-identifiable cases from the data analysis. BEHAVIOR.md section 16 gives
the order to change things in, and the commands.
