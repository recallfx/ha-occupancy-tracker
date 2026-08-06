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

Each area also exposes a non-authoritative `Activity` binary sensor. It is ON
while trusted live motion evidence exists or for the room profile's hold time
after motion or contact activity: 20 seconds for transition spaces, 120 seconds
for living areas, and 900 seconds for sleeping areas.
It may turn OFF while a person is still present, so it is never an input to the
durable occupancy decision. The periodic refresh is ten seconds.

Room profiles also tune freshness decay and warning windows. They never tune
the durable indoor latch. A corridor can therefore become quiet quickly without
being falsely declared vacant, while a bedroom avoids warnings during sleep.

## Event flow

1. Home Assistant reports a configured sensor state change.
2. `OccupancyCoordinator` preserves both the sensor's source timestamp and the
   local receipt timestamp, updates `SensorState`, and records a `MapSnapshot`.
3. `MapOccupancyResolver` processes the event.
4. Trusted positive indoor evidence is added to `indoor_latched`.
5. Adjacency plausibility may produce a warning but cannot reject the event.
6. The conservative union is applied to `AreaState`.
7. Home Assistant entities are updated.

Repeated motion ON events are useful presence refreshes. Unavailable, unreliable, or stuck sensor states are not treated as current positive evidence.
Events older than the latest accepted source time for that sensor, or with an
implausibly future timestamp, are audited and ignored instead of rewinding live
state. Startup ON states are aged against their receipt time for stuck-sensor
detection.

Magnetic edges are recorded separately as contact activity. They are useful
context but do not fabricate motion evidence or latch a room occupied.

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

The **Clear Stale Occupancy** button calls `clear_stale_indoor_occupancy` for
every latched area. The `occupancy_tracker.clear_stale_occupancy` service
requires an `area_id` and targets only that room.

Both paths clear only indoor latches whose trusted sensors are currently OFF.
Areas with positive sensor evidence remain occupied during manual cleanup.

## Evidence diagnostics

Each area exposes an `evidence_state`:

- `active`: at least one trusted configured motion/person sensor is ON;
- `stale`: indoor occupancy is latched but no trusted sensor is currently ON;
- `inferred`: compatibility state for occupied data without live evidence or an indoor latch;
- `vacant`: no current occupancy evidence.
- `unknown`: durable indoor state was not restored and no later evidence or
  explicit clear has established it.

It also exposes `active_sensors`, `last_positive_evidence`, `stale_since`, and
`cleared_by`. `freshness` is a time-since-motion score, not a calibrated
probability. The old `probability` attribute and coordinator method remain as
compatibility aliases.

## Replay and persistence

`MapStateRecorder` records a bounded event history in memory and supports
non-mutating deterministic verification during the current integration
lifetime. This bounded history is not an authority for clearing live state.

The conservative state is stored in Home Assistant's versioned storage under
`occupancy_tracker.occupancy_state`. Each indoor area is persisted as
`possible`, `cleared`, or `unknown`, together with its last motion/contact
evidence where available. Missing or unreadable storage leaves indoor areas
unknown rather than claiming vacancy.

Startup ordering is deliberate:

1. Load persistent occupancy.
2. Restore possible, explicitly cleared, and unknown indoor states plus evidence timestamps.
3. Record the restoration for deterministic in-memory replay.
4. Process current Home Assistant sensor states.

An initial OFF state does not clear restored occupancy. A current trusted ON state may add or refresh live evidence. Explicit stale cleanup updates both replay history and persistent storage.

## Logging and audit

Routine events without occupancy changes are DEBUG-only in the operational log.
The separate JSONL audit records every sensor decision, including ignored
duplicates, source and receipt timestamps, occupancy/latch deltas, availability,
trust changes, restore/clear/reset actions, and warning open/resolve events.
Manual clear records its actor and any rooms refused because a trusted sensor is
still active. Handlers are installed idempotently so a setup retry does not
duplicate every line.

## Lighting boundary

Durable occupancy and short-lived activity answer different questions.
Automatic lighting must use raw motion sensors or the `Activity` entity for its
OFF timeout. The occupancy entity answers whether someone could still be
present and therefore deliberately may not emit an automatic OFF edge. The
current motion-light configuration continues to use raw PIR signals.

## Main components

- `coordinator.py`: sensor ingestion, periodic checks, entity updates, and explicit cleanup.
- `helpers/map_occupancy_resolver.py`: trusted live evidence and indoor latch.
- `helpers/anomaly_detector.py`: read-only warnings and adjacency plausibility.
- `helpers/history_verifier.py`: deterministic replay verification and live-state preservation.
- `sensors/area_sensors.py`: durable occupancy and short-lived activity entities.
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
7. Activity expiration cannot clear occupancy.
8. Targeted cleanup cannot clear any room other than the requested area.
