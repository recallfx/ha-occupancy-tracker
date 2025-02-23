class OccupancyTracker:
    def __init__(self, config):
        self.config = config
        self.adjacency = config.get("adjacency", {})
        self.sensors = config.get("sensors", {})

        self.occupant_count = {}
        self.occupant_prob = {}
        # Track last motion time for adjacency checks
        self.last_motion_time = {}
        for area in config.get("areas", {}):
            self.occupant_count[area] = 0.0
            self.occupant_prob[area] = 0.05
            self.last_motion_time[area] = None

        # short time threshold to interpret quick adjacency transitions
        self.short_threshold = 5

    def set_occupancy(self, area, count):
        self.occupant_count[area] = float(count)
        self.occupant_prob[area] = 0.95 if count > 0 else 0.05

    def get_occupancy(self, area):
        return self.occupant_count.get(area, 0.0)

    def get_occupancy_probability(self, area):
        return self.occupant_prob.get(area, 0.05)

    def updateTimestamp(self, new_time):
        # By default, no time-based occupant removal unless triggered by sensor logic
        pass

    def process_event(self, sensor_name, state, timestamp=0):
        sensor_info = self.sensors.get(sensor_name)
        if not sensor_info:
            return

        sensor_type = sensor_info.get("type")
        sensor_areas = sensor_info.get("area")
        if isinstance(sensor_areas, str):
            sensor_areas = [sensor_areas]

        if sensor_type in ["motion", "camera_motion", "camera_person"]:
            if state:
                self._handle_motion_start(sensor_areas, timestamp)
            else:
                self._handle_motion_stop(sensor_areas, timestamp)
        elif sensor_type == "magnetic":
            if state:
                self._handle_magnetic_trigger(sensor_areas, timestamp)

    def _handle_motion_start(self, areas, timestamp):
        for area in areas:
            self.last_motion_time[area] = timestamp

            # If occupant_count(area) is 0 => see if occupant can come from adjacency
            if self.occupant_count[area] == 0.0:
                self._try_move_in(area, timestamp)
            else:
                # occupant already present => bump probability
                self.occupant_prob[area] = 0.95

    def _handle_motion_stop(self, areas, timestamp):
        """If occupant is in an exit-capable area and motion stops,
        we might interpret that occupant as having left the system.
        Or if there's an old area with occupant_count >=1, we degrade probability slightly.
        """
        for area in areas:
            occ = self.occupant_count[area]
            if occ >= 1:
                # drop prob from 0.95 to something lower but not 0
                if self.occupant_prob[area] > 0.5:
                    self.occupant_prob[area] = 0.75

                # If this area is outdoors (and marked exit_capable in config) => occupant leaves
                if self._area_is_exit_capable(area):
                    # occupant presumably left
                    self.occupant_count[area] = 0.0
                    self.occupant_prob[area] = 0.05
            # if occupant_count < 1 => do nothing special

    def _handle_magnetic_trigger(self, areas, timestamp):
        """For a sensor bridging two areas (e.g. entrance <-> backyard),
        if there's occupant(s) in one side, we move exactly one occupant to the other side.
        Additional triggers can move more occupants if multiple are present.
        """
        if len(areas) == 1:
            # treat like a single motion sensor
            self._handle_motion_start(areas, timestamp)
            return

        areaA, areaB = areas
        countA = self.occupant_count[areaA]
        countB = self.occupant_count[areaB]

        if countA >= 1:
            # move 1 occupant from A to B
            self.occupant_count[areaA] = countA - 1
            self.occupant_count[areaB] = countB + 1
            self.occupant_prob[areaA] = 0.95 if self.occupant_count[areaA] > 0 else 0.05
            self.occupant_prob[areaB] = 0.95
        elif countB >= 1:
            # move 1 occupant from B to A
            self.occupant_count[areaB] = countB - 1
            self.occupant_count[areaA] = countA + 1
            self.occupant_prob[areaB] = 0.95 if self.occupant_count[areaB] > 0 else 0.05
            self.occupant_prob[areaA] = 0.95
        # no occupant on either side => interpret as 1 occupant arrives from outside?
        # to keep it simpler, do a +1 occupant on side A or partial.
        # We'll do: occupant_count[A] = 1 if both are 0
        elif countA == 0 and countB == 0:
            self.occupant_count[areaA] = 1
            self.occupant_prob[areaA] = 0.95

    def _try_move_in(self, new_area, timestamp):
        """Attempt to move occupant from an adjacent area if time is short.
        If no occupant is adjacent, we interpret it as a new occupant arriving.
        """
        adj_list = self.adjacency.get(new_area, [])
        best_candidate = None
        best_dt = None

        for old_area in adj_list:
            old_count = self.occupant_count[old_area]
            if old_count > 0:
                dt = abs(timestamp - (self.last_motion_time[old_area] or 0))
                if best_candidate is None or dt < best_dt:
                    best_candidate = old_area
                    best_dt = dt

        # if no candidate => occupant_count(new_area)=1
        if not best_candidate:
            self.occupant_count[new_area] = 1.0
            self.occupant_prob[new_area] = 0.95
            return

        # if candidate found but dt is large => occupant_count(new_area)=1
        if best_dt is None or best_dt > self.short_threshold:
            self.occupant_count[new_area] = 1.0
            self.occupant_prob[new_area] = 0.95
            return

        # occupant_count(best_candidate) >= 1 => move exactly 1 occupant
        self.occupant_count[best_candidate] -= 1
        self.occupant_count[best_candidate] = max(
            self.occupant_count[best_candidate], 0
        )
        self.occupant_prob[best_candidate] = (
            0.95 if self.occupant_count[best_candidate] > 0 else 0.05
        )

        self.occupant_count[new_area] = self.occupant_count[new_area] + 1
        self.occupant_prob[new_area] = 0.95

    def _area_is_exit_capable(self, area):
        """If your config.yaml or code designates certain areas (frontyard/backyard) as exit_capable,
        we can automatically remove occupant from the system once motion is cleared.
        """
        area_config = self.config.get("areas", {}).get(area, {})
        return area_config.get("exit_capable", False)
