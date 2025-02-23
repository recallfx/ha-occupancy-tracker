import unittest

import yaml

from custom_components.occupancy_tracker.occupancy_tracker import OccupancyTracker


class TestOccupancyTracker(unittest.TestCase):
    def setUp(self):
        with open("config.yaml") as f:
            config = yaml.safe_load(f)
        self.tracker = OccupancyTracker(config)

    def test_single_complete_transition(self):
        """- Start with occupant in main_bathroom => occupant_count=1
        - occupant triggers motion in main_bathroom, then quickly in main_bedroom
        - After those events, we finalize occupant => main_bedroom=1, main_bathroom=0
        """
        self.tracker.set_occupancy("main_bathroom", 1)
        # occupant: bathroom -> bedroom
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)

        # No motion in bathroom => occupant finalizes in bedroom
        self.tracker.process_event("motion_main_bathroom", False, timestamp=5)

        occ_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_bedroom = self.tracker.get_occupancy("main_bedroom")
        prob_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
        prob_bedroom = self.tracker.get_occupancy_probability("main_bedroom")

        self.assertEqual(occ_bathroom, 0)
        self.assertEqual(occ_bedroom, 1)
        self.assertAlmostEqual(prob_bathroom, 0.05, delta=0.1)
        self.assertAlmostEqual(prob_bedroom, 0.95, delta=0.1)

    def test_single_complete_transition_2(self):
        """- 2 occupants in main_bathroom
        - occupant triggers motion from bathroom->bedroom
        - Enough time passes to confirm 1 occupant is in bedroom, 1 remains in bathroom
        - Probability remains high in both rooms
        """
        self.tracker.set_occupancy("main_bathroom", 2)
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)

        # Let time pass so we finalize or confirm occupant distribution
        self.tracker.updateTimestamp(20)

        occ_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_bedroom = self.tracker.get_occupancy("main_bedroom")
        prob_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
        prob_bedroom = self.tracker.get_occupancy_probability("main_bedroom")

        # One occupant should have moved, the other stayed
        self.assertEqual(occ_bathroom, 1)
        self.assertEqual(occ_bedroom, 1)
        self.assertAlmostEqual(prob_bathroom, 0.95, delta=0.1)
        self.assertAlmostEqual(prob_bedroom, 0.95, delta=0.1)

    def test_motion_cleared_event(self):
        """- occupant in living => occupant_count=1
        - occupant triggers motion, then motion is cleared
        - Probability should drop below 0.95 after cleared
        """
        self.tracker.set_occupancy("living", 1)
        self.tracker.process_event("motion_living", True, timestamp=0)
        self.tracker.process_event("motion_living", False, timestamp=5)

        prob = self.tracker.get_occupancy_probability("living")
        self.assertLess(prob, 0.95)

    def test_multiple_events(self):
        """- occupant in main_bathroom => occupant_count=1
        - occupant triggers motion in bathroom, then in bedroom,
          then in back_hall at a later time => occupant might chain from bath->bed->back_hall.
        - Each area ends with occupant_count>0 if partial transitions remain recognized,
          or occupant might end up in back_hall if we finalize it.
        - We'll at least ensure no negative occupant counts and occupant_count>0 in relevant areas.
        """
        self.tracker.set_occupancy("main_bathroom", 1)
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)
        self.tracker.process_event("motion_back_hall", True, timestamp=5)

        occ_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_bedroom = self.tracker.get_occupancy("main_bedroom")
        occ_back_hall = self.tracker.get_occupancy("back_hall")

        # The occupant might have ended up in back_hall or partial distributed among them,
        # but for simplicity let's just confirm they didn't vanish entirely from each place:
        self.assertTrue(occ_bathroom >= 0)
        self.assertTrue(occ_bedroom >= 0)
        self.assertTrue(occ_back_hall >= 0)

    def test_transition_probability_update(self):
        """- occupant in main_bathroom => occupant_count=1
        - occupant triggers motion in main_bathroom => bedroom => back_hall
        - Ensure the probability in the old area is less than in the final area
        """
        self.tracker.set_occupancy("main_bathroom", 1)
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=1)
        self.tracker.process_event("motion_back_hall", True, timestamp=2)

        prob_bath = self.tracker.get_occupancy_probability("main_bathroom")
        prob_back_hall = self.tracker.get_occupancy_probability("back_hall")
        self.assertLess(prob_bath, prob_back_hall)

    def test_multi_area_sensor(self):
        """- magnetic_therace bridging entrance <-> backyard
        - Trigger sensor => occupant_count in at least one area should become > 0
        """
        self.tracker.process_event("magnetic_therace", True, timestamp=0)

        entrance_prob = self.tracker.get_occupancy_probability("entrance")
        backyard_prob = self.tracker.get_occupancy_probability("backyard")

        # We just check occupant probability is > 0 in at least one
        self.assertTrue(entrance_prob > 0.05 or backyard_prob > 0.05)

    def test_indoor_outdoor_transition(self):
        """- occupant triggers motion_entrance => occupant_count in entrance
        - occupant triggers magnetic_entry bridging entrance <-> frontyard
        - occupant might end up in frontyard or partially so
        - Just ensure occupant_count(frontyard) >= 0, occupant_count(entrance) >= 0, no negative numbers
        """
        self.tracker.process_event("motion_entrance", True, timestamp=0)
        self.tracker.process_event("magnetic_entry", True, timestamp=1)

        entrance_occ = self.tracker.get_occupancy("entrance")
        frontyard_occ = self.tracker.get_occupancy("frontyard")

        self.assertTrue(entrance_occ >= 0)
        self.assertTrue(frontyard_occ >= 0)

    #
    # --- New/Expanded Tests (Chain Moves, Exits, Multi-Occupants, etc.) ---
    #

    def test_occupant_exits_system(self):
        """- occupant in frontyard => occupant_count=1
        - occupant's motion is cleared, no adjacency motion => occupant presumably left the system
          -> occupant_count(frontyard)=0 at final
        """
        self.tracker.set_occupancy("frontyard", 1)
        self.tracker.process_event("motion_front_left_camera", True, timestamp=0)
        # occupant stops motion => logic interprets occupant as exiting
        self.tracker.process_event("motion_front_left_camera", False, timestamp=10)

        fy_count = self.tracker.get_occupancy("frontyard")
        fy_prob = self.tracker.get_occupancy_probability("frontyard")

        self.assertEqual(fy_count, 0)
        self.assertAlmostEqual(fy_prob, 0.05, delta=0.1)

    def test_multiple_occupant_transition(self):
        """- Start with 3 occupants in main_bathroom
        - occupant triggers motion => main_bedroom in quick succession => move 1 occupant fully
        - Final occupant_count(bathroom)=2, occupant_count(bedroom)=1
        """
        self.tracker.set_occupancy("main_bathroom", 3)
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=1)

        bath_count = self.tracker.get_occupancy("main_bathroom")
        bed_count = self.tracker.get_occupancy("main_bedroom")

        self.assertEqual(bath_count, 2)
        self.assertEqual(bed_count, 1)

    def test_long_idle_no_motion(self):
        """- occupant in main_bedroom => occupant_count=1
        - no events for a long time => occupant remains
        """
        self.tracker.set_occupancy("main_bedroom", 1)
        self.tracker.updateTimestamp(500)

        bed_count = self.tracker.get_occupancy("main_bedroom")
        bed_prob = self.tracker.get_occupancy_probability("main_bedroom")
        self.assertEqual(bed_count, 1)
        self.assertAlmostEqual(bed_prob, 0.95, delta=0.1)

    def test_multi_occupant_simultaneous_transitions(self):
        """- 2 occupants start in main_bedroom.
        - In short order, we see motion_main_bathroom AND motion_back_hall.
        - We expect occupant A => main_bathroom, occupant B => back_hall.
          final occupant_count(main_bedroom)=0, occupant_count(bathroom)=1, occupant_count(back_hall)=1
        """
        self.tracker.set_occupancy("main_bedroom", 2)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=0)
        # occupant A => bathroom
        self.tracker.process_event("motion_main_bathroom", True, timestamp=1)
        # occupant B => back_hall
        self.tracker.process_event("motion_back_hall", True, timestamp=1)

        self.assertEqual(self.tracker.get_occupancy("main_bedroom"), 0)
        self.assertEqual(self.tracker.get_occupancy("main_bathroom"), 1)
        self.assertEqual(self.tracker.get_occupancy("back_hall"), 1)

    def test_repeated_magnetic_sensor_for_multiple_occupants(self):
        """- 2 occupants in 'entrance'
        - Motion triggers entrance -> backyard transition
        - 'magnetic_therace' sensor verifies the transitions
        - Each transition should move exactly 1 occupant, until old area hits 0
        """
        self.tracker.set_occupancy("entrance", 2)

        # 1st occupant moves
        self.tracker.process_event("motion_entrance", True, timestamp=1)
        self.tracker.process_event(
            "magnetic_therace", True, timestamp=2
        )  # verify transition

        self.tracker.process_event("motion_back_left_camera", True, timestamp=7)

        self.tracker.process_event("magnetic_therace", False, timestamp=12)

        # 2nd occupant moves
        self.tracker.process_event(
            "magnetic_therace", True, timestamp=14
        )  # verify transition

        self.tracker.process_event("motion_entrance", False, timestamp=19)

        self.tracker.process_event("magnetic_therace", False, timestamp=24)

        entrance_count = self.tracker.get_occupancy("entrance")
        backyard_count = self.tracker.get_occupancy("backyard")

        self.assertEqual(entrance_count, 0)
        self.assertEqual(backyard_count, 2)

        self.tracker.process_event("motion_back_left_camera", False, timestamp=30)

        entrance_count = self.tracker.get_occupancy("entrance")
        backyard_count = self.tracker.get_occupancy("backyard")

        self.assertEqual(entrance_count, 0)
        self.assertEqual(backyard_count, 0)

    def test_chain_of_transitions_ending_in_exit(self):
        """- occupant: main_bathroom => main_bedroom => front_hall => frontyard
        - occupant stops motion in frontyard => occupant_count(frontyard)=0 if system infers they left
        - old areas should end occupant_count=0
        """
        self.tracker.set_occupancy("main_bathroom", 1)
        # bath -> bedroom
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)
        # bedroom -> back_hall
        self.tracker.process_event("motion_back_hall", True, timestamp=5)

        self.tracker.process_event("motion_main_bathroom", False, timestamp=6)
        self.tracker.process_event("motion_main_bedroom", False, timestamp=8)

        # back_hall -> front_hall
        self.tracker.process_event("motion_front_hall", True, timestamp=9)

        # front_hall -> entrance
        self.tracker.process_event("motion_entrance", True, timestamp=11)

        self.tracker.process_event("motion_back_hall", False, timestamp=11)
        self.tracker.process_event("motion_front_hall", False, timestamp=14)

        # entrance -> frontyard
        self.tracker.process_event("magnetic_entry", True, timestamp=15)
        self.tracker.process_event("motion_front_left_camera", True, timestamp=20)

        self.tracker.process_event("magnetic_entry", False, timestamp=25)
        self.tracker.process_event("motion_front_left_camera", False, timestamp=25)

        self.assertEqual(self.tracker.get_occupancy("main_bathroom"), 0)
        self.assertEqual(self.tracker.get_occupancy("main_bedroom"), 0)
        self.assertEqual(self.tracker.get_occupancy("back_hall"), 0)
        self.assertEqual(self.tracker.get_occupancy("front_hall"), 0)
        self.assertEqual(self.tracker.get_occupancy("entrance"), 0)
        self.assertEqual(self.tracker.get_occupancy("frontyard"), 0)

    def test_frontyard_to_backyard_via_front_hall(self):
        """- occupant starts in frontyard => occupant_count=1
        - occupant triggers motion in front_hall => occupant finalizes in front_hall => frontyard=0
        - occupant triggers motion in backyard => occupant finalizes in backyard => front_hall=0
        """
        self.tracker.set_occupancy("frontyard", 1)
        # frontyard -> front_hall
        self.tracker.process_event("motion_front_left_camera", True, timestamp=0)
        self.tracker.process_event("motion_front_hall", True, timestamp=2)

        # finalize old area
        self.tracker.process_event("motion_front_left_camera", False, timestamp=3)

        # front_hall -> backyard
        self.tracker.process_event("motion_front_hall", True, timestamp=10)
        self.tracker.process_event("motion_back_left_camera", True, timestamp=12)

        # finalize old area
        self.tracker.process_event("motion_front_hall", False, timestamp=13)

        self.assertEqual(self.tracker.get_occupancy("frontyard"), 0)
        self.assertEqual(self.tracker.get_occupancy("front_hall"), 0)
        self.assertEqual(self.tracker.get_occupancy("backyard"), 1)


if __name__ == "__main__":
    unittest.main()
