"""Log formatting utilities for occupancy tracking.

Provides clear, compact logging with letter notation for areas.

Notation:
- Areas use first letter(s): Y=frontyard, E=entrance, F=front_hall, etc.
- Occupancy: @ suffix with count (e.g., E@2 = entrance has 2 people)
- Motion: + = sensor active (e.g., E+ = entrance sensor ON)
- Door: / = open (e.g., Y/E = door between Y and E is open)
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .area_state import AreaState
    from .sensor_state import SensorState


class LogFormatter:
    """Formats occupancy state for logging."""

    # Default area abbreviations (can be overridden)
    DEFAULT_ABBREVS = {
        "frontyard": "Y",
        "backyard": "Bk",
        "entrance": "E",
        "front_hall": "F",
        "back_hall": "B",
        "living": "L",
        "living_room": "L",
        "kitchen": "K",
        "main_bedroom": "M",
        "main_bathroom": "Mb",
        "bedroom": "Br",
        "bedroom_1": "B1",
        "bedroom_2": "B2",
        "bathroom": "Ba",
        "area_a": "A",
        "area_b": "B",
        "area_c": "C",
    }

    def __init__(
        self, areas: Dict[str, "AreaState"], sensors: Dict[str, "SensorState"]
    ):
        self.areas = areas
        self.sensors = sensors
        self._abbrevs = self._build_abbreviations()

    def _build_abbreviations(self) -> Dict[str, str]:
        """Build area abbreviations, avoiding conflicts."""
        abbrevs = {}
        used = set()

        for area_id in self.areas:
            # Check default first
            if area_id in self.DEFAULT_ABBREVS:
                abbrev = self.DEFAULT_ABBREVS[area_id]
            else:
                # Generate from area name
                abbrev = self._generate_abbrev(area_id, used)

            # Handle conflicts
            if abbrev in used:
                abbrev = self._make_unique(abbrev, used)

            abbrevs[area_id] = abbrev
            used.add(abbrev)

        return abbrevs

    def _generate_abbrev(self, area_id: str, used: set) -> str:
        """Generate abbreviation from area ID."""
        # Try first letter capitalized
        parts = area_id.replace("_", " ").split()
        if len(parts) == 1:
            return area_id[0].upper()
        # Multi-word: use initials
        return "".join(p[0].upper() for p in parts)

    def _make_unique(self, abbrev: str, used: set) -> str:
        """Make abbreviation unique by adding numbers."""
        i = 2
        while f"{abbrev}{i}" in used:
            i += 1
        return f"{abbrev}{i}"

    def abbrev(self, area_id: str) -> str:
        """Get abbreviation for area."""
        return self._abbrevs.get(area_id, area_id[:3].title())

    def sensor_name(self, sensor_id: str) -> str:
        """Get short sensor name (just the last part after last underscore or dot)."""
        # binary_sensor.motion_entrance -> motion_entrance
        name = sensor_id.split(".")[-1]
        # motion_entrance -> entrance (remove common prefixes)
        for prefix in ["motion_", "person_", "magnetic_"]:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
        return name

    def format_state(self, active_sensors: Optional[set] = None) -> str:
        """
        Format current occupancy state as compact string.

        Example: "Y E@ F B+@2 M L"
        - E@ = entrance occupied (1 person)
        - B+@2 = back_hall sensor active, 2 people
        """
        if active_sensors is None:
            active_sensors = self._get_active_sensors()

        parts = []
        for area_id in sorted(self.areas.keys(), key=lambda x: self._abbrevs.get(x, x)):
            area = self.areas[area_id]
            abbrev = self.abbrev(area_id)

            # Check if any sensor in this area is active
            is_active = any(
                self._sensor_in_area(sid, area_id) for sid in active_sensors
            )

            occ = area.occupancy

            if is_active and occ > 0:
                if occ == 1:
                    parts.append(f"{abbrev}+@")
                else:
                    parts.append(f"{abbrev}+@{occ}")
            elif is_active:
                parts.append(f"{abbrev}+")
            elif occ > 0:
                if occ == 1:
                    parts.append(f"{abbrev}@")
                else:
                    parts.append(f"{abbrev}@{occ}")
            else:
                parts.append(abbrev)

        return " ".join(parts)

    def format_occupied_only(self) -> str:
        """Format only occupied areas."""
        parts = []
        for area_id, area in self.areas.items():
            if area.occupancy > 0:
                abbrev = self.abbrev(area_id)
                if area.occupancy == 1:
                    parts.append(f"{abbrev}@")
                else:
                    parts.append(f"{abbrev}@{area.occupancy}")
        return " ".join(parts) if parts else "(empty)"

    def format_move(
        self, source_id: str, target_id: str, old_source_occ: int, old_target_occ: int
    ) -> str:
        """Format a movement between areas."""
        src = self.abbrev(source_id)
        tgt = self.abbrev(target_id)
        src_area = self.areas.get(source_id)
        tgt_area = self.areas.get(target_id)

        new_source_occ = src_area.occupancy if src_area else 0
        new_target_occ = tgt_area.occupancy if tgt_area else 0

        return f"{src}→{tgt} ({src}:{old_source_occ}→{new_source_occ}, {tgt}:{old_target_occ}→{new_target_occ})"

    def format_entry(self, area_id: str, source: str = "outside") -> str:
        """Format an entry event."""
        abbrev = self.abbrev(area_id)
        area = self.areas.get(area_id)
        occ = area.occupancy if area else 0
        return f"→{abbrev} (from {source}, now {occ})"

    def format_sensor_trigger(
        self, sensor_id: str, state: bool, area_ids: List[str]
    ) -> str:
        """Format a sensor trigger event."""
        name = self.sensor_name(sensor_id)
        state_str = "ON" if state else "OFF"
        areas = ",".join(self.abbrev(a) for a in area_ids if a in self._abbrevs)
        return f"{name}({areas})→{state_str}"

    def format_occupancy_changes(
        self, old_occupancy: Dict[str, int], new_occupancy: Dict[str, int]
    ) -> str:
        """Format occupancy changes between old and new state."""
        changes = []
        for area_id in set(old_occupancy.keys()) | set(new_occupancy.keys()):
            old_occ = old_occupancy.get(area_id, 0)
            new_occ = new_occupancy.get(area_id, 0)
            if old_occ != new_occ:
                abbrev = self.abbrev(area_id)
                changes.append(f"{abbrev}:{old_occ}→{new_occ}")
        return " ".join(changes)

    def format_state_view(
        self, areas: Dict[str, "AreaState"], sensors: Dict[str, "SensorState"]
    ) -> str:
        """Format state view (update internal state first)."""
        self.areas = areas
        self.sensors = sensors
        return self.format_state()

    def _get_active_sensors(self) -> set:
        """Get set of currently active sensor IDs."""
        return {sid for sid, s in self.sensors.items() if s.is_trusted_active}

    def _sensor_in_area(self, sensor_id: str, area_id: str) -> bool:
        """Check if sensor belongs to area."""
        sensor = self.sensors.get(sensor_id)
        if not sensor:
            return False
        sensor_areas = sensor.config.get("area", [])
        if isinstance(sensor_areas, str):
            sensor_areas = [sensor_areas]
        return area_id in sensor_areas
