from __future__ import annotations

import logging
from collections import deque
from typing import Dict, Iterable, List, Optional, Tuple

from .anomaly_detector import AnomalyDetector
from .area_state import AreaState
from .constants import MAGNETIC_SENSOR_TYPES, MOTION_SENSOR_TYPES, normalize_area_ids
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
    RECENT_MOTION_WINDOW = 60.0       # Area considered "recently active"
    CLUSTER_MERGE_WINDOW = 10.0       # Max gap to merge adjacent activations into one chain
    RETENTION_TIMEOUT = 28800.0       # 8 hours — max retention without motion
    RETENTION_HOUSE_QUIET_GUARD = 600.0  # 10 min — if house quiet this long, don't clear
    EXIT_AREA_TIMEOUT = 300.0         # Exit-capable areas auto-clear after 5 min
    OUTDOOR_INTRUSION_WINDOW = 300.0  # Magnetic evidence window
    BOOTSTRAP_WINDOW = 120.0          # After restart, allow any indoor activation for 2 min
    RETAINED_INACTIVITY_TIMEOUT = 120.0  # Clear retained rooms after 2 min of no motion
    RECENTLY_OCCUPIED_WINDOW = 300.0  # Accept re-activation of rooms occupied within 5 min
    SENSOR_CYCLING_GUARD = 15.0          # Protect retained areas with recent motion (covers KNX 5s cycle)
    MIN_RETENTION_COOLDOWN = 10.0        # Minimum seconds before a retained area can be displaced/cleaned

    def __init__(self, config: OccupancyTrackerConfig) -> None:
        self.adjacency_map = self._build_adjacency(config)
        self.open_plan_groups: Dict[str, List[str]] = self._build_open_plan_groups(config)
        self.area_to_group: Dict[str, str] = {}
        for gid, members in self.open_plan_groups.items():
            for aid in members:
                self.area_to_group[aid] = gid
        self.max_occupants: int = config.get("max_occupants", 3) if isinstance(config, dict) else 3
        self.retained: Dict[str, float] = {}  # area_id -> retention start timestamp
        self._first_activation_time: float = 0.0  # Timestamp of the very first sensor activation

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
            if not sensor.current_state:
                continue
            if sensor.config.get("type", "") not in MOTION_SENSOR_TYPES:
                continue
            active.update(sensor.area_ids)
        return active

    def _is_area_active(self, area_id: str, sensors: Dict[str, SensorState]) -> bool:
        """Check if any MOTION/CAMERA sensor in the area is currently ON."""
        for sensor in sensors.values():
            if not sensor.current_state:
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
            if not sensor.current_state:
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
                    sensor, timestamp, areas, sensors, anomaly_detector,
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
            event = self._parse_sensor_event(snapshot)
            if event:
                sensor_id, new_state = event
                sensor = sensors.get(sensor_id)
                if sensor:
                    sensor.update_state(new_state, snapshot.timestamp)
            self.process_snapshot(snapshot, areas, sensors, anomaly_detector)

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
        area_id = sensor.config.get("area")
        if not area_id:
            return None

        area = areas.get(area_id)
        if not area:
            return None

        area.last_motion = timestamp

        # Track the very first activation for bootstrap window calculation
        if self._first_activation_time == 0.0:
            self._first_activation_time = timestamp

        # If this area was retained, refresh retention timestamp
        if area_id in self.retained:
            self.retained[area_id] = timestamp

        # Rebuild clusters and set occupancy
        self._rebuild_occupancy(timestamp, areas, sensors, anomaly_detector)

        return area_id if area.occupied else None

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
        area_id = sensor.config.get("area")
        if not area_id:
            return None

        area = areas.get(area_id)
        if not area:
            return None

        area.last_off = timestamp

        # If other motion sensors in this area are still ON, skip rebuild
        if self._any_other_motion_sensor_active(area_id, sensor.id, sensors):
            _LOGGER.debug(
                f"Motion-OFF in {area_id}: other sensor still active, skipping"
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
        # PHASE 4: Cap at max_occupants
        # =============================================
        if len(occupied_areas) > self.max_occupants:
            ranked = sorted(
                occupied_areas,
                key=lambda aid: areas[aid].last_motion,
                reverse=True,
            )
            occupied_areas = set(ranked[: self.max_occupants])

        # =============================================
        # PHASE 5: Manage retention
        # =============================================
        previously_occupied = {aid for aid, a in areas.items() if a.occupied}

        # Identify which occupied leaders came from retained (not from an
        # active sensor). These are candidates for displacement.
        # Areas with active sensors are sensor-based even if also retained —
        # the sensor being ON is strong evidence the person is there.
        retained_leaders = (occupied_areas & set(self.retained.keys())) - sensor_active_areas

        # Displacement: if a retained leader can reach a sensor leader via
        # a monotonically-increasing motion chain, the person moved — UNLESS
        # the sensor leader is reachable from an exit-capable area that was
        # activated after the retained person settled (indicating a different
        # person entered from outside).
        sensor_based_leaders = occupied_areas - retained_leaders
        # When multiple retained leaders exist (indicating multiple people),
        # restrict displacement to direct neighbors only — multi-hop
        # paths through stale intermediates are ambiguous and risk
        # displacing the wrong person.
        multi_retained = len(retained_leaders) > 1
        displaced: set[str] = set()
        for ret_leader in retained_leaders:
            # Never displace a retained area whose sensor is currently ON —
            # the person is clearly still there (e.g., sitting in study
            # while someone else walks through corridor).
            if ret_leader in sensor_active_areas:
                continue

            # Sensor cycling guard: if area had motion very recently, the
            # sensor is likely just in its OFF gap (KNX 5s cycle).  Don't
            # displace — wait for the sensor to come back ON.
            ret_area = areas[ret_leader]
            if ret_area.last_motion > 0 and (timestamp - ret_area.last_motion) <= self.SENSOR_CYCLING_GUARD:
                continue

            # Retention cooldown: don't displace areas that were only
            # recently retained — give the sensor time to re-detect.
            retention_start = self.retained.get(ret_leader, 0)
            if retention_start > 0 and (timestamp - retention_start) < self.MIN_RETENTION_COOLDOWN:
                continue

            max_d = 1 if multi_retained else 6
            if not self._can_reach_sensor_leader(
                ret_leader, sensor_based_leaders, areas, timestamp,
                max_depth=max_d,
            ):
                continue

            # Check if the sensor leaders have independent entry evidence
            ret_motion = areas[ret_leader].last_motion
            independent = self._has_independent_entry_evidence(
                sensor_based_leaders, ret_leader, ret_motion, areas, timestamp,
            )
            if not independent:
                displaced.add(ret_leader)

        occupied_areas -= displaced
        for area_id in displaced:
            if area_id in self.retained:
                del self.retained[area_id]

        # Build set of areas that are in the same cluster as an occupied leader
        # These are "trail" areas — person walked through, not staying
        trail_areas: set[str] = set()
        for cluster in clusters:
            leader_in_cluster = cluster & occupied_areas
            if leader_in_cluster:
                trail_areas |= (cluster - leader_in_cluster)

        # Areas that lost occupancy — start retention
        for area_id in previously_occupied - occupied_areas:
            area = areas[area_id]
            if area.is_exit_capable:
                continue
            if area.is_transition:
                continue
            if area_id in trail_areas:
                continue
            if area_id in displaced:
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

        # Cap again after adding retained
        if len(final_occupied) > self.max_occupants:
            ranked = sorted(
                final_occupied,
                key=lambda aid: areas[aid].last_motion,
                reverse=True,
            )
            final_occupied = set(ranked[: self.max_occupants])
            for area_id in set(self.retained.keys()) - final_occupied:
                del self.retained[area_id]

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
            _LOGGER.debug(f"Rebuild @ {timestamp:.1f}: occupied={occ}, retained={set(self.retained.keys())}")

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
                and (timestamp - neighbor.last_occupied_at) <= self.RECENTLY_OCCUPIED_WINDOW
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

        # Bootstrap: two modes.
        # 1. Standard: very first activation ever (no motion recorded anywhere).
        #    Accepts unconditionally — system just started, need to seed.
        # 2. Extended: within BOOTSTRAP_WINDOW of first activation AND fewer
        #    people tracked than max_occupants. Allows multiple people to
        #    register after HA restart without needing adjacent evidence.
        total_occupied = sum(1 for a in areas.values() if a.occupied)
        total_tracked = total_occupied + len(self.retained)

        # Standard bootstrap: no activation has EVER occurred
        if self._first_activation_time == 0.0 and self.adjacency_map.get(area_id):
            return True

        # Extended bootstrap: within time window, room for more people
        if (
            total_tracked < self.max_occupants
            and self._first_activation_time > 0
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
        # Prefer non-transition areas with very recent motion
        non_transition = [
            aid for aid in cluster
            if not areas[aid].is_transition
            and (timestamp - areas[aid].last_motion) <= self.CLUSTER_MERGE_WINDOW
        ]
        if non_transition:
            return max(non_transition, key=lambda aid: areas[aid].last_motion)

        # Fall back to any non-transition area
        non_transition_all = [
            aid for aid in cluster if not areas[aid].is_transition
        ]
        if non_transition_all:
            return max(non_transition_all, key=lambda aid: areas[aid].last_motion)

        # All transition — pick most recent
        return max(cluster, key=lambda aid: areas[aid].last_motion)

    # ------------------------------------------------------------------
    # Displacement: BFS from retained leader to sensor-based leader
    # ------------------------------------------------------------------

    def _can_reach_sensor_leader(
        self,
        start_id: str,
        sensor_leaders: set[str],
        areas: Dict[str, AreaState],
        timestamp: float,
        max_depth: int = 6,
    ) -> bool:
        """BFS from a retained leader through recently-active areas to a sensor leader.

        Follows intermediates that have motion within RECENT_MOTION_WINDOW of
        the current timestamp.  Skips retained areas (other people sitting
        still).  The sensor leader must have more recent motion than the
        retained area.
        """
        if not sensor_leaders:
            return False

        ret_motion = areas[start_id].last_motion

        visited: set[str] = {start_id}
        frontier = deque([(start_id, 0)])

        while frontier:
            current, depth = frontier.popleft()
            if depth >= max_depth:
                continue

            for neighbor_id in self.adjacency_map.get(current, []):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                neighbor = areas.get(neighbor_id)
                if not neighbor:
                    continue

                # Sensor leader: must have more recent motion than retained
                if neighbor_id in sensor_leaders:
                    if neighbor.last_motion > ret_motion:
                        return True
                    continue

                # Skip retained areas (other people sitting still)
                if neighbor_id in self.retained:
                    continue

                # Intermediate must have recent motion within window
                if neighbor.last_motion == 0:
                    continue
                if (timestamp - neighbor.last_motion) > self.RECENT_MOTION_WINDOW:
                    continue

                frontier.append((neighbor_id, depth + 1))

        return False

    def _has_independent_entry_evidence(
        self,
        sensor_leaders: set[str],
        retained_id: str,
        ret_motion: float,
        areas: Dict[str, AreaState],
        timestamp: float,
    ) -> bool:
        """Check if any sensor leader is reachable from an exit-capable area
        that was activated after the retained person settled.

        BFS backward from each sensor leader through areas with recent motion
        that is more recent than ret_motion and within RECENT_MOTION_WINDOW.
        The BFS does NOT pass through the retained area.  If it finds an
        exit-capable area with motion > ret_motion, a different person entered.
        """
        visited: set[str] = {retained_id}
        frontier = deque()
        retained_neighbors = set(self.adjacency_map.get(retained_id, []))
        for lid in sensor_leaders:
            if lid not in visited:
                visited.add(lid)
                frontier.append(lid)
                # Check leader itself — exit-capable with fresh motion.
                # BUT if the retained area is a direct neighbor, the most
                # likely explanation is the same person walked next door
                # (e.g., bedroom → hall), not a new entry from outside.
                leader = areas.get(lid)
                if (
                    leader
                    and leader.is_exit_capable
                    and leader.last_motion > ret_motion
                    and lid not in retained_neighbors
                ):
                    return True

        while frontier:
            current = frontier.popleft()

            for neighbor_id in self.adjacency_map.get(current, []):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)

                neighbor = areas.get(neighbor_id)
                if not neighbor:
                    continue

                # Found exit-capable evidence
                if (
                    neighbor.is_exit_capable
                    and neighbor.last_motion > ret_motion
                    and (timestamp - neighbor.last_motion)
                    <= self.RECENT_MOTION_WINDOW
                ):
                    return True

                # Follow areas with motion more recent than retained
                if neighbor.last_motion == 0:
                    continue
                if neighbor.last_motion <= ret_motion:
                    continue
                if (timestamp - neighbor.last_motion) > self.RECENT_MOTION_WINDOW:
                    continue

                frontier.append(neighbor_id)

        return False

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _has_leaving_evidence(
        self, area_id: str, areas: Dict[str, AreaState]
    ) -> bool:
        """Check if an adjacent area has motion more recent than this area's.

        This indicates the person walked out to a neighbor.
        """
        area = areas[area_id]
        for neighbor_id in self.adjacency_map.get(area_id, []):
            neighbor = areas.get(neighbor_id)
            if neighbor and neighbor.last_motion > area.last_motion:
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
            and (area.last_motion == 0 or (timestamp - area.last_motion) > self.RETENTION_HOUSE_QUIET_GUARD)
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
            if area.last_motion > 0 and (timestamp - area.last_motion) <= self.SENSOR_CYCLING_GUARD:
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
