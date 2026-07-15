from __future__ import annotations

import logging
from collections import deque
from typing import Dict, Iterable, List, Optional, Tuple

from .anomaly_detector import AnomalyDetector
from .area_state import AreaState
from .constants import MAGNETIC_SENSOR_TYPES, MOTION_SENSOR_TYPES
from .map_state_recorder import MapSnapshot
from .sensor_state import SensorState
from .types import OccupancyTrackerConfig


_LOGGER = logging.getLogger("resolver")


class MapOccupancyResolver:
    """
    Activity-clustering occupancy resolver (v3).

    CORE PRINCIPLES:
    1. Motion ON = someone is there. Update timestamp, rebuild clusters.
    2. Motion OFF ≠ person left. Person stays until evidence shows they moved.
    3. The LAST activated area in a chain of adjacent activations is where the person IS.
    4. Separate clusters of simultaneous activity = separate people.
    5. Open-plan areas with overlapping sensors form one detection zone.
    6. Retained areas preserve occupancy for sleeping/sitting still.
    """

    # Timing constants
    RECENT_MOTION_WINDOW = 60.0  # Area considered "recently active"
    CLUSTER_MERGE_WINDOW = 10.0  # Max gap to merge adjacent activations into one chain
    RETENTION_TIMEOUT = 28800.0  # 8 hours — max retention without motion
    RETENTION_HOUSE_QUIET_GUARD = (
        600.0  # 10 min — if house quiet this long, don't clear
    )
    EXIT_AREA_TIMEOUT = 300.0  # Exit-capable areas auto-clear after 5 min
    OUTDOOR_INTRUSION_WINDOW = 300.0  # Magnetic evidence window
    BOOTSTRAP_WINDOW = 120.0  # After restart, allow any indoor activation for 2 min
    RETAINED_INACTIVITY_TIMEOUT = 120.0  # Clear retained rooms after 2 min of no motion
    RECENTLY_OCCUPIED_WINDOW = (
        300.0  # Accept re-activation of rooms occupied within 5 min
    )
    SENSOR_CYCLING_GUARD = (
        15.0  # Protect retained areas with recent motion (covers KNX 5s cycle)
    )
    MIN_RETENTION_COOLDOWN = (
        10.0  # Minimum seconds before a retained area can be displaced/cleaned
    )
    DEPARTURE_COUPLING_WINDOW = (
        15.0  # Neighbor motion must closely follow room motion to imply departure
    )

    def __init__(self, config: OccupancyTrackerConfig) -> None:
        self.adjacency_map = self._build_adjacency(config)
        self._area_order = {
            area_id: index for index, area_id in enumerate(config.get("areas", {}))
        }
        self.open_plan_groups: Dict[str, List[str]] = self._build_open_plan_groups(
            config
        )
        self.area_to_group: Dict[str, str] = {}
        for gid, members in self.open_plan_groups.items():
            for aid in members:
                self.area_to_group[aid] = gid
        self.retained: Dict[str, float] = {}  # area_id -> retention start timestamp
        self._first_activation_time: float = (
            0.0  # Timestamp of the very first sensor activation
        )

    def reset(self) -> None:
        self.retained.clear()
        self._first_activation_time = 0.0

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_adjacency(config: OccupancyTrackerConfig) -> Dict[str, List[str]]:
        adjacency = config.get("adjacency", {}) if isinstance(config, dict) else {}
        normalized: Dict[str, List[str]] = {}
        for area_id, neighbors in adjacency.items():
            area_list = normalized.setdefault(area_id, [])
            for neighbor in neighbors:
                if neighbor not in area_list:
                    area_list.append(neighbor)
                reverse_list = normalized.setdefault(neighbor, [])
                if area_id not in reverse_list:
                    reverse_list.append(area_id)
        return normalized

    @staticmethod
    def _build_open_plan_groups(config: OccupancyTrackerConfig) -> Dict[str, List[str]]:
        groups = config.get("open_plan_groups", {}) if isinstance(config, dict) else {}
        result: Dict[str, List[str]] = {}
        for gid, gconfig in groups.items():
            if isinstance(gconfig, dict):
                result[gid] = gconfig.get("areas", [])
            elif isinstance(gconfig, list):
                result[gid] = gconfig
        return result

    # ------------------------------------------------------------------
    # Sensor state queries
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sensor_active_areas(sensors: Dict[str, SensorState]) -> set[str]:
        """Compute the set of area IDs that have at least one active motion/camera sensor."""
        active: set[str] = set()
        for sensor in sensors.values():
            if not sensor.is_trusted_active:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            active.update(sensor.area_ids)
        return active

    def _is_area_active(self, area_id: str, sensors: Dict[str, SensorState]) -> bool:
        """Check if any MOTION/CAMERA sensor in the area is currently ON."""
        for sensor in sensors.values():
            if not sensor.is_trusted_active:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            if area_id in sensor.area_ids:
                return True
        return False

    def _any_other_motion_sensor_active(
        self, area_id: str, exclude_sensor_id: str, sensors: Dict[str, SensorState]
    ) -> bool:
        for sensor in sensors.values():
            if sensor.id == exclude_sensor_id:
                continue
            if not sensor.is_trusted_active:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            if area_id in sensor.area_ids:
                return True
        return False

    # ------------------------------------------------------------------
    # Snapshot processing
    # ------------------------------------------------------------------

    def _parse_sensor_event(self, snapshot: MapSnapshot) -> Optional[Tuple[str, bool]]:
        if snapshot.event_type != "sensor" or not snapshot.description:
            return None
        parts = snapshot.description.split(":")
        if len(parts) != 3 or parts[0] != "sensor":
            return None
        sensor_id = parts[1]
        new_state = parts[2] == "on"
        return sensor_id, new_state

    @staticmethod
    def _parse_availability_event(
        snapshot: MapSnapshot,
    ) -> Optional[Tuple[str, bool]]:
        if snapshot.event_type != "availability" or not snapshot.description:
            return None
        parts = snapshot.description.split(":")
        if len(parts) != 3 or parts[0] != "availability":
            return None
        return parts[1], parts[2] == "available"

    @staticmethod
    def _parse_baseline_event(
        snapshot: MapSnapshot,
    ) -> Optional[Tuple[str, bool]]:
        if snapshot.event_type != "baseline" or not snapshot.description:
            return None
        parts = snapshot.description.split(":")
        if len(parts) != 3 or parts[0] != "baseline":
            return None
        return parts[1], parts[2] == "on"

    @staticmethod
    def _restore_snapshot_sensor_metadata(
        snapshot: MapSnapshot,
        sensors: Dict[str, SensorState],
    ) -> None:
        """Restore non-physical trust state for deterministic replay."""
        for sensor_id, data in snapshot.sensors.items():
            sensor = sensors.get(sensor_id)
            if not sensor:
                continue
            sensor.is_available = data.get("available", sensor.is_available)
            sensor.is_reliable = data.get("reliable", sensor.is_reliable)
            sensor.is_stuck = data.get("stuck", sensor.is_stuck)

    def process_snapshot(
        self,
        snapshot: MapSnapshot,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> Optional[str]:
        """Apply a single snapshot event to update occupancy state."""
        event = self._parse_sensor_event(snapshot)
        if not event:
            return None

        sensor_id, new_state = event
        sensor = sensors.get(sensor_id)
        if not sensor:
            _LOGGER.debug("Sensor %s not tracked; skipping snapshot", sensor_id)
            return None

        sensor_type = sensor.config.get("type", "")
        timestamp = snapshot.timestamp

        if sensor_type in MOTION_SENSOR_TYPES:
            if new_state:
                return self._handle_motion_on(
                    sensor,
                    timestamp,
                    areas,
                    sensors,
                    anomaly_detector,
                )
            else:
                return self._handle_motion_off(sensor, timestamp, areas, sensors)

        elif sensor_type in MAGNETIC_SENSOR_TYPES:
            return self._handle_magnetic_event(sensor, new_state, timestamp, areas)

        return None

    def recalculate_from_history(
        self,
        snapshots: Iterable[MapSnapshot],
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> None:
        """Rebuild occupancy entirely from history."""
        history = sorted(list(snapshots), key=lambda snap: snap.timestamp)
        self.reset()

        for area in areas.values():
            area.occupancy = 0
            area.last_motion = 0
            area.last_off = 0
            area.activity_history = []

        for snapshot in history:
            baseline = self._parse_baseline_event(snapshot)
            if baseline:
                sensor_id, state = baseline
                sensor = sensors.get(sensor_id)
                if sensor:
                    sensor.seed_state(state, snapshot.timestamp)
                self._restore_snapshot_sensor_metadata(snapshot, sensors)
                continue

            availability = self._parse_availability_event(snapshot)
            if availability:
                sensor_id, is_available = availability
                sensor = sensors.get(sensor_id)
                if sensor:
                    sensor.is_available = is_available
                self._restore_snapshot_sensor_metadata(snapshot, sensors)
                continue

            event = self._parse_sensor_event(snapshot)
            if event:
                sensor_id, new_state = event
                sensor = sensors.get(sensor_id)
                if sensor:
                    sensor.update_state(new_state, snapshot.timestamp)
            self.process_snapshot(snapshot, areas, sensors, anomaly_detector)
            self._restore_snapshot_sensor_metadata(snapshot, sensors)

    # ------------------------------------------------------------------
    # Magnetic events
    # ------------------------------------------------------------------

    def _handle_magnetic_event(
        self,
        sensor: SensorState,
        new_state: bool,
        timestamp: float,
        areas: Dict[str, AreaState],
    ) -> Optional[str]:
        """Handle magnetic sensor events (doors/windows)."""
        for area_id in sensor.area_ids:
            area = areas.get(area_id)
            if area:
                area.last_motion = timestamp
                _LOGGER.debug(f"Magnetic event on {sensor.id} kept {area_id} active")
        return None

    # ------------------------------------------------------------------
    # Motion-ON handler
    # ------------------------------------------------------------------

    def _handle_motion_on(
        self,
        sensor: SensorState,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector],
    ) -> Optional[str]:
        """Handle motion sensor turning ON — update timestamp and rebuild clusters."""
        area_ids = [area_id for area_id in sensor.area_ids if area_id in areas]
        if not area_ids:
            return None

        for area_id in area_ids:
            areas[area_id].last_motion = timestamp

        # Track the very first activation for bootstrap window calculation
        if self._first_activation_time == 0.0:
            self._first_activation_time = timestamp

        # If this area was retained, refresh retention timestamp
        for area_id in area_ids:
            if area_id in self.retained:
                self.retained[area_id] = timestamp

        # Rebuild clusters and set occupancy
        self._rebuild_occupancy(timestamp, areas, sensors, anomaly_detector)

        return next((area_id for area_id in area_ids if areas[area_id].occupied), None)

    # ------------------------------------------------------------------
    # Motion-OFF handler
    # ------------------------------------------------------------------

    def _handle_motion_off(
        self,
        sensor: SensorState,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> Optional[str]:
        """Handle motion sensor turning OFF — rebuild clusters if all sensors off."""
        area_ids = [area_id for area_id in sensor.area_ids if area_id in areas]
        if not area_ids:
            return None

        for area_id in area_ids:
            areas[area_id].last_off = timestamp

        # If other motion sensors in this area are still ON, skip rebuild
        if len(area_ids) == 1 and self._any_other_motion_sensor_active(
            area_ids[0], sensor.id, sensors
        ):
            _LOGGER.debug(
                "Motion-OFF in %s: other sensor still active, skipping", area_ids[0]
            )
            return None

        # Rebuild clusters
        self._rebuild_occupancy(timestamp, areas, sensors)

        return None

    # ------------------------------------------------------------------
    # Core: Cluster rebuild
    # ------------------------------------------------------------------

    def _rebuild_occupancy(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> None:
        """Rebuild occupancy from scratch using activity clustering."""

        # Pre-compute sensor-active areas once for the entire rebuild.
        # This avoids O(sensors) iteration on every _is_area_active call.
        sensor_active_areas = self._compute_sensor_active_areas(sensors)

        # =============================================
        # PHASE 0: Clean stale retentions BEFORE building active areas
        # =============================================
        self._clean_retained(timestamp, areas, sensor_active_areas)

        # =============================================
        # PHASE 1: Identify active areas
        # =============================================
        active_areas = self._build_active_areas(
            timestamp, areas, sensors, anomaly_detector
        )

        # =============================================
        # PHASE 2: Build adjacency clusters
        # =============================================
        clusters = self._build_clusters(active_areas, areas, timestamp)

        # =============================================
        # PHASE 2b: Force-merge open-plan groups
        # =============================================
        clusters = self._merge_open_plan(clusters)

        # =============================================
        # PHASE 3: Determine occupied area per cluster
        # =============================================
        occupied_areas = set()
        for cluster in clusters:
            if not cluster:
                continue
            leader = self._pick_leader(cluster, areas, timestamp)
            occupied_areas.add(leader)

        # =============================================
        # PHASE 5: Manage retention
        # =============================================
        previously_occupied = {aid for aid, a in areas.items() if a.occupied}

        # Build set of areas that are in the same cluster as an occupied leader
        # These are "trail" areas — person walked through, not staying
        trail_areas: set[str] = set()
        for cluster in clusters:
            leader_in_cluster = cluster & occupied_areas
            if leader_in_cluster:
                trail_areas |= cluster - leader_in_cluster

        # Areas that lost occupancy — start retention
        for area_id in previously_occupied - occupied_areas:
            area = areas[area_id]
            if area.is_exit_capable:
                continue
            if area.is_transition:
                continue
            if area_id in trail_areas:
                continue
            grp = self.area_to_group.get(area_id)
            if grp is not None:
                group_has_leader = any(
                    self.area_to_group.get(occ) == grp for occ in occupied_areas
                )
                if group_has_leader:
                    continue
            # Don't retain if the area has been inactive for too long AND
            # a neighbor has more recent motion (evidence the person left).
            # This prevents re-retaining areas that Phase 0 just cleaned.
            if (
                area.last_motion > 0
                and (timestamp - area.last_motion) > self.RETAINED_INACTIVITY_TIMEOUT
                and self._has_leaving_evidence(area_id, areas)
            ):
                continue
            self.retained[area_id] = timestamp

        # Remove from retained when area is a trail area (in someone else's
        # cluster, not the leader).  Do NOT remove just because the sensor
        # came ON — the retained flag protects against merging with a
        # different person's walking cluster.
        for area_id in list(self.retained.keys()):
            if area_id in trail_areas:
                del self.retained[area_id]

        # =============================================
        # PHASE 6: Final occupancy including retained
        # =============================================
        final_occupied = occupied_areas | set(self.retained.keys())

        # =============================================
        # PHASE 7: Apply to area state
        # =============================================
        for area_id, area in areas.items():
            area.occupied = area_id in final_occupied
            area.cluster_id = None
            for i, cluster in enumerate(clusters):
                if area_id in cluster:
                    area.cluster_id = i
                    break

        if _LOGGER.isEnabledFor(logging.DEBUG):
            occ = {aid for aid, a in areas.items() if a.occupied}
            _LOGGER.debug(
                f"Rebuild @ {timestamp:.1f}: occupied={occ}, retained={set(self.retained.keys())}"
            )

    # ------------------------------------------------------------------
    # Phase 1: Build active areas with phantom rejection
    # ------------------------------------------------------------------

    def _build_active_areas(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
        anomaly_detector: Optional[AnomalyDetector] = None,
    ) -> set[str]:
        active_areas: set[str] = set()

        for area_id, area in areas.items():
            is_sensor_on = self._is_area_active(area_id, sensors)

            if not is_sensor_on:
                continue

            if not self._has_plausible_source(area_id, timestamp, areas, sensors):
                if anomaly_detector:
                    anomaly_detector.record_unexpected_activation(
                        area_id, None, timestamp, context="no_plausible_source"
                    )
                continue

            active_areas.add(area_id)

        # Add retained areas
        for area_id in self.retained:
            active_areas.add(area_id)

        return active_areas

    def _has_plausible_source(
        self,
        area_id: str,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensors: Dict[str, SensorState],
    ) -> bool:
        area = areas[area_id]

        # Exit-capable areas can always have new arrivals
        if area.is_exit_capable:
            return True

        # With no neighbors, this area's own sensor is the only possible source.
        if not self.adjacency_map.get(area_id):
            return True

        # Already occupied or retained
        if area.occupied or area_id in self.retained:
            return True

        # Recently occupied: if this area was occupied within 5 minutes,
        # it's a known-active area, not a phantom. Accept re-activation
        # immediately (e.g., after displacement during sensor OFF gap).
        if (
            area.last_occupied_at > 0
            and (timestamp - area.last_occupied_at) <= self.RECENTLY_OCCUPIED_WINDOW
        ):
            return True

        # Check adjacent areas
        for neighbor_id in self.adjacency_map.get(area_id, []):
            neighbor = areas.get(neighbor_id)
            if not neighbor:
                continue

            if self._is_area_active(neighbor_id, sensors):
                return True

            if (
                neighbor.last_motion > 0
                and (timestamp - neighbor.last_motion) <= self.CLUSTER_MERGE_WINDOW
            ):
                return True

            if neighbor.occupied:
                return True

            if (
                neighbor.last_occupied_at > 0
                and (timestamp - neighbor.last_occupied_at)
                <= self.RECENTLY_OCCUPIED_WINDOW
            ):
                return True

            if neighbor_id in self.retained:
                return True

        # Check magnetic evidence
        for sensor_state in sensors.values():
            if sensor_state.config.get("type", "") not in MAGNETIC_SENSOR_TYPES:
                continue
            if area_id not in sensor_state.area_ids:
                continue
            if sensor_state.last_changed and sensor_state.last_changed >= (
                timestamp - self.OUTDOOR_INTRUSION_WINDOW
            ):
                return True

        # Bootstrap: accept indoor activations during the startup window.
        # Room occupancy is deliberately not capped by an estimated number
        # of people; several sensors may legitimately represent one person.
        if self._first_activation_time == 0.0 and self.adjacency_map.get(area_id):
            return True

        if (
            self._first_activation_time > 0
            and (timestamp - self._first_activation_time) <= self.BOOTSTRAP_WINDOW
            and area.is_indoors
            and self.adjacency_map.get(area_id)
        ):
            return True

        # Persistent activation: if this area's sensor has been cycling
        # ON/OFF repeatedly, it's a real person, not a phantom.
        # Phantoms fire once or twice. A person sitting causes 5+ cycles.
        if area.is_indoors and self.adjacency_map.get(area_id):
            recent_activations = 0
            for s in sensors.values():
                s_type = s.config.get("type", "")
                if s_type not in MOTION_SENSOR_TYPES:
                    continue
                if area_id not in s.area_ids:
                    continue
                # Count ON events in history within the last 5 minutes
                for item in s.history:
                    if item.state and (timestamp - item.timestamp) <= 300:
                        recent_activations += 1
            if recent_activations >= 2:
                _LOGGER.info(
                    f"Persistent activation in {area_id}: "
                    f"{recent_activations} activations in 5min, accepting"
                )
                return True

        return False

    # ------------------------------------------------------------------
    # Phase 2: Build clusters via BFS
    # ------------------------------------------------------------------

    def _build_clusters(
        self,
        active_areas: set[str],
        areas: Dict[str, AreaState],
        timestamp: float,
    ) -> list[set[str]]:
        clusters: list[set[str]] = []
        visited: set[str] = set()

        for area_id in active_areas:
            if area_id in visited:
                continue
            cluster: set[str] = set()
            queue = deque([area_id])
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                cluster.add(current)

                for neighbor_id in self.adjacency_map.get(current, []):
                    if neighbor_id in visited:
                        continue
                    if neighbor_id not in active_areas:
                        continue
                    if self._should_merge(current, neighbor_id, areas, timestamp):
                        queue.append(neighbor_id)

            clusters.append(cluster)

        return clusters

    def _should_merge(
        self,
        area_a: str,
        area_b: str,
        areas: Dict[str, AreaState],
        timestamp: float,
    ) -> bool:
        """Determine if two adjacent active areas should be in the same cluster."""
        # Open-plan areas always merge
        grp_a = self.area_to_group.get(area_a)
        grp_b = self.area_to_group.get(area_b)
        if grp_a is not None and grp_a == grp_b:
            return True

        # If either is retained, don't merge — a retained area represents
        # a person sitting still and should not be absorbed into a different
        # person's walking cluster.
        if area_a in self.retained or area_b in self.retained:
            return False

        a = areas[area_a]
        b = areas[area_b]
        time_gap = abs(a.last_motion - b.last_motion)

        return time_gap <= self.CLUSTER_MERGE_WINDOW

    # ------------------------------------------------------------------
    # Phase 2b: Force-merge open-plan groups
    # ------------------------------------------------------------------

    def _merge_open_plan(self, clusters: list[set[str]]) -> list[set[str]]:
        for group_id, group_members in self.open_plan_groups.items():
            group_cluster_indices: set[int] = set()
            for i, cluster in enumerate(clusters):
                for member in group_members:
                    if member in cluster:
                        group_cluster_indices.add(i)
                        break

            if len(group_cluster_indices) > 1:
                merged: set[str] = set()
                for i in group_cluster_indices:
                    merged |= clusters[i]
                for i in sorted(group_cluster_indices, reverse=True):
                    clusters.pop(i)
                clusters.append(merged)

        return clusters

    # ------------------------------------------------------------------
    # Phase 3: Pick leader per cluster
    # ------------------------------------------------------------------

    def _pick_leader(
        self,
        cluster: set[str],
        areas: Dict[str, AreaState],
        timestamp: float,
    ) -> str:
        """Pick the occupied area within a cluster (most recent non-transition)."""

        def leader_key(area_id: str) -> tuple[float, int]:
            return (
                areas[area_id].last_motion,
                -self._area_order.get(area_id, len(self._area_order)),
            )

        # Prefer non-transition areas with very recent motion
        non_transition = [
            aid
            for aid in cluster
            if not areas[aid].is_transition
            and (timestamp - areas[aid].last_motion) <= self.CLUSTER_MERGE_WINDOW
        ]
        if non_transition:
            return max(non_transition, key=leader_key)

        # Fall back to any non-transition area
        non_transition_all = [aid for aid in cluster if not areas[aid].is_transition]
        if non_transition_all:
            return max(non_transition_all, key=leader_key)

        # All transition — pick most recent
        return max(cluster, key=leader_key)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _has_leaving_evidence(self, area_id: str, areas: Dict[str, AreaState]) -> bool:
        """Check for tightly coupled room-to-neighbor motion."""
        area = areas[area_id]
        for neighbor_id in self.adjacency_map.get(area_id, []):
            neighbor = areas.get(neighbor_id)
            if not neighbor or neighbor.last_motion <= area.last_motion:
                continue
            if (
                neighbor.last_motion - area.last_motion
                <= self.DEPARTURE_COUPLING_WINDOW
            ):
                return True
        return False

    # ------------------------------------------------------------------
    # Phase 5: Clean retained
    # ------------------------------------------------------------------

    def _clean_retained(
        self,
        timestamp: float,
        areas: Dict[str, AreaState],
        sensor_active_areas: set[str],
    ) -> None:
        """Remove retained areas that should no longer be occupied.

        Args:
            sensor_active_areas: Pre-computed set of area IDs with active sensors.
        """
        # Check if the entire house is quiet
        house_quiet = all(
            area_id not in sensor_active_areas
            and (
                area.last_motion == 0
                or (timestamp - area.last_motion) > self.RETENTION_HOUSE_QUIET_GUARD
            )
            for area_id, area in areas.items()
        )

        stale = []
        for area_id, retention_start in self.retained.items():
            area = areas[area_id]

            # Absolute timeout
            if (timestamp - retention_start) > self.RETENTION_TIMEOUT:
                stale.append(area_id)
                continue

            # Exit-capable shorter timeout
            if area.is_exit_capable:
                if (timestamp - area.last_motion) > self.EXIT_AREA_TIMEOUT:
                    stale.append(area_id)
                    continue

            # If house is quiet, don't clear anyone (people sleeping)
            if house_quiet:
                continue

            # Retention cooldown: don't clean areas that were only recently
            # retained — give the sensor time to re-detect the person.
            if (timestamp - retention_start) < self.MIN_RETENTION_COOLDOWN:
                continue

            # Sensor cycling guard: if area had motion very recently, the
            # sensor is likely just in its OFF gap.
            if (
                area.last_motion > 0
                and (timestamp - area.last_motion) <= self.SENSOR_CYCLING_GUARD
            ):
                continue

            # Inactivity cleanup: only clear if BOTH conditions are met:
            # 1. No motion in this area for RETAINED_INACTIVITY_TIMEOUT (120s)
            # 2. An adjacent area has motion MORE RECENT than this area's
            #    last_motion — evidence the person walked out.
            if (
                area.last_motion > 0
                and (timestamp - area.last_motion) > self.RETAINED_INACTIVITY_TIMEOUT
                and self._has_leaving_evidence(area_id, areas)
            ):
                stale.append(area_id)
                continue

        for area_id in stale:
            del self.retained[area_id]
