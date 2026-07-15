# Occupancy Tracker Architecture

## Safety contract

The primary output answers: **could someone still be in this area?**

For indoor areas, false occupied is acceptable. False vacant is not. Therefore:

- A trusted motion or person sensor turning ON marks every configured indoor area occupied immediately.
- Adjacency and movement plausibility may create warnings, but cannot reject positive evidence.
- Sensor OFF means "no motion detected," not "room empty."
- Motion elsewhere, inactivity, freshness decay, and inferred departure cannot clear indoor occupancy.
- Only the explicit **Clear Stale Occupancy** action clears an indoor latch, and it preserves areas whose trusted sensor is currently ON.

Outdoor areas are not latched. They become vacant when trusted live motion/person evidence ends.

## State model

`AreaState.occupancy` is boolean, exposed as `0` or `1` for compatibility.

The resolver has one durable safety concept: `indoor_latched`. Once trusted
positive indoor evidence exists, that area remains occupied until explicit
cleanup. Outdoor occupancy follows current trusted sensor evidence and is not
latched.

The final occupied set is simply the union of current trusted active areas and
conservative indoor latches. Adjacency is diagnostic only; there are no cluster
leaders, movement caps, or inferred departures in the occupancy decision.

## Event flow

1. Home Assistant reports a configured sensor state change.
2. `OccupancyCoordinator` updates `SensorState` and records a `MapSnapshot`.
3. `MapOccupancyResolver` processes the event.
4. Trusted positive indoor evidence is added to `indoor_latched`.
5. Adjacency plausibility may produce a warning but cannot reject the event.
6. The conservative union is applied to `AreaState`.
7. Home Assistant entities are updated.

Repeated motion ON events are useful presence refreshes. Unavailable, unreliable, or stuck sensor states are not treated as current positive evidence.

## Anomaly handling

`AnomalyDetector` is read-only: it reports suspicious state but never clears
occupancy.

Examples:

- motion without a plausible adjacent source;
- a sensor that appears stuck;
- extended indoor occupancy;
- occupancy that looks stale because motion, neighbors, and magnetic sensors are all quiet.

The last case creates `phantom_occupancy_suspected`. It is deliberately diagnostic: the same observations also describe a sleeping occupant or a failed PIR.

Outdoor occupancy ends when its trusted live sensor evidence ends.

## Explicit cleanup

The **Clear Stale Occupancy** button calls `clear_stale_indoor_occupancy`.

It clears only indoor latches whose trusted sensors are currently OFF. Areas with positive sensor evidence remain occupied even during manual cleanup.

## Evidence diagnostics

Each area exposes an `evidence_state`:

- `active`: at least one trusted configured motion/person sensor is ON;
- `stale`: indoor occupancy is latched but no trusted sensor is currently ON;
- `inferred`: compatibility state for occupied data without live evidence or an indoor latch;
- `vacant`: no current occupancy evidence.

It also exposes `active_sensors`, `last_positive_evidence`, `stale_since`, and
`cleared_by`. `freshness` is a time-since-motion score, not a calibrated
probability. The old `probability` attribute and coordinator method remain as
compatibility aliases.

## Replay and persistence

`MapStateRecorder` records events in memory and supports deterministic replay during the current integration lifetime.

The conservative latch is also stored in Home Assistant's versioned storage under `occupancy_tracker.occupancy_state`. The durable payload contains only latched indoor area IDs and their last persisted motion timestamps.

Startup ordering is deliberate:

1. Load persistent occupancy.
2. Restore indoor latches and last-motion timestamps.
3. Record the restoration for deterministic in-memory replay.
4. Process current Home Assistant sensor states.

An initial OFF state does not clear restored occupancy. A current trusted ON state may add or refresh live evidence. Explicit stale cleanup updates both replay history and persistent storage.

## Lighting boundary

Durable occupancy and short-lived lighting activity are different signals.
Automatic lighting must use raw motion sensors (or a separate activity signal)
for its OFF timeout. The occupancy entity answers whether someone could still
be present and therefore deliberately may not emit an automatic OFF edge.

## Main components

- `coordinator.py`: sensor ingestion, periodic checks, entity updates, and explicit cleanup.
- `helpers/map_occupancy_resolver.py`: trusted live evidence, indoor latch, and adjacency warnings.
- `helpers/anomaly_detector.py`: read-only warnings.
- `helpers/area_state.py`: boolean area state.
- `helpers/sensor_state.py`: sensor trust, availability, and history.
- `helpers/map_state_recorder.py`: in-memory event snapshots and replay.

## Required invariants

Tests must preserve these rules:

1. Trusted sensor ON immediately produces occupancy.
2. Every area of a multi-area ON sensor is occupied.
3. Simultaneously active adjacent areas may all be occupied.
4. Unexplained motion warns but is not rejected.
5. Sensor OFF, neighbor motion, departure trails, and timeouts cannot clear indoor occupancy.
6. Manual cleanup clears stale latches but preserves active sensor evidence.
