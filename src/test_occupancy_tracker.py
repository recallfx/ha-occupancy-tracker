import unittest

import yaml

from occupancy_tracker import OccupancyTracker


class TestOccupancyTracker(unittest.TestCase):
    def setUp(self):
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
        self.tracker = OccupancyTracker(config)

    #  Case: Occupant moves from main_bathroom to main_bedroom but time 
    #  between events is too short make conclusions, thus we divide occupancy
    #  between both areas, but the probability of occupancy should be higher
    #  in the area where the occupant is expected to be
    def test_single_incomplete_transition(self):
        self.tracker.set_occupancy("main_bathroom", 1)
        # Occupant moves from main_bathroom to main_bedroom
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)        
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)
        occ_main_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_main_bedroom = self.tracker.get_occupancy("main_bedroom")
        prob_main_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
        prob_main_bedroom = self.tracker.get_occupancy_probability("main_bedroom")
        
        self.assertEqual(occ_main_bathroom, occ_main_bedroom)
        self.assertEqual(occ_main_bathroom, 0.5)

        self.assertLess(prob_main_bathroom, prob_main_bedroom)
        self.assertAlmostEqual(prob_main_bedroom, 0.95, delta=0.1)


    #  Case: One occupant moves from main_bathroom to main_bedroom another stays
    #  in main_bedroom. But the time between events is too short make conclusions,
    #  thus we divide occupancy between both areas, but the probability of occupancy
    #  should still be the same in both areas as we make assumption that only one moved.
    #  Apply probabilistic split
    def test_single_incomplete_transition_2(self):
        self.tracker.set_occupancy("main_bathroom", 2)
        # One occupant moves from main_bathroom to main_bedroom
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)        
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)
        occ_main_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_main_bedroom = self.tracker.get_occupancy("main_bedroom")
        prob_main_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
        prob_main_bedroom = self.tracker.get_occupancy_probability("main_bedroom")
        
        self.assertEqual(occ_main_bathroom, occ_main_bedroom)
        self.assertEqual(occ_main_bathroom, 1)

        self.assertAlmostEqual(prob_main_bathroom, 0.95, delta=0.1)
        self.assertAlmostEqual(prob_main_bedroom, 0.95, delta=0.1)



    # def test_motion_expected_to_clear(self):
    #     self.tracker.set_occupancy("main_bathroom", 1)
    #     # Occupant moves from main_bathroom to main_bedroom
    #     self.tracker.process_event("motion_main_bathroom", True, timestamp=0)  

    #     # Usually motion clears after 5 seconds os inactivity   
    #     self.tracker.process_event("motion_main_bedroom", True, timestamp=6)

    #     prob_main_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
    #     prob_main_bedroom = self.tracker.get_occupancy_probability("main_bedroom")
    #     # Probability of occupancy should decrease drastically
    #     self.assertAlmostEqual(prob_main_bathroom, 0.60, delta=0.1)
    #     self.assertAlmostEqual(prob_main_bedroom, 0.95, delta=0.1)
        
    # def test_transition_with_someone_staying(self):
    #     self.tracker.set_occupancy("main_bathroom", 1)
    #     # Occupant moves from main_bathroom to main_bedroom
    #     self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
    #     # But another stays in main_bedroom
    #     self.tracker.process_event("motion_main_bedroom", True, timestamp=200)

    #     prob_main_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
    #     prob_main_bedroom = self.tracker.get_occupancy_probability("main_bedroom")
    #     self.assertAlmostEqual(prob_main_bathroom, 0.85, delta=0.1)
    #     self.assertAlmostEqual(prob_main_bedroom, 0.95, delta=0.1)
        

    # Case: Occupant moves from main_bathroom to main_bedroom and then
    # leaves main_bedroom. We should see a decrease in occupancy in main_bedroom
    # and an increase in occupancy in main_bathroom.
    # Probabilities should be updated accordingly
    def test_single_complete_transition(self):
        self.tracker.set_occupancy("main_bathroom", 1)
        # Occupant moves from main_bathroom to main_bedroom
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)        
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)
        # No motion in main_bathroom
        self.tracker.process_event("motion_main_bathroom", False, timestamp=5)

        occ_main_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_main_bedroom = self.tracker.get_occupancy("main_bedroom")
        prob_main_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
        prob_main_bedroom = self.tracker.get_occupancy_probability("main_bedroom")
        
        self.assertEqual(occ_main_bathroom, 0)
        self.assertEqual(occ_main_bedroom, 1)

        self.assertAlmostEqual(prob_main_bathroom, 0.05, delta=0.1)
        self.assertAlmostEqual(prob_main_bedroom, 0.95, delta=0.1)

    #  Case: One occupant moves from main_bathroom to main_bedroom another stays
    #  in main_bedroom. Time between events is enough to make conclusion, that both rooms are occupied,
    #  thus we should see occupancy in both rooms and probability of occupancy should be the same in both rooms.
    def test_single_complete_transition_2(self):
        self.tracker.set_occupancy("main_bathroom", 2)
        # Occupant moves from main_bathroom to main_bedroom
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)        
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)

        self.tracker.updateTimestamp(20)

        occ_main_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_main_bedroom = self.tracker.get_occupancy("main_bedroom")
        prob_main_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
        prob_main_bedroom = self.tracker.get_occupancy_probability("main_bedroom")
        
        self.assertEqual(occ_main_bathroom, occ_main_bedroom)
        self.assertEqual(occ_main_bathroom, 1)

        self.assertAlmostEqual(prob_main_bathroom, 0.95, delta=0.1)
        self.assertAlmostEqual(prob_main_bedroom, 0.95, delta=0.1)


    def test_motion_cleared_event(self):
        self.tracker.set_occupancy("living", 1)
        # Motion cleared event after timeout
        self.tracker.process_event("motion_living", True, timestamp=0)
        self.tracker.process_event("motion_living", False, timestamp=5)
        prob = self.tracker.get_occupancy_probability("living")
        self.assertLess(prob, 0.95)

    def test_multiple_events(self):
        self.tracker.set_occupancy("main_bathroom", 1)
        # Two simultaneous transitions
        self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
        self.tracker.process_event("motion_main_bedroom", True, timestamp=2)
        self.tracker.process_event("motion_back_hall", True, timestamp=5)

        occ_main_bathroom = self.tracker.get_occupancy("main_bathroom")
        occ_main_bedroom = self.tracker.get_occupancy("main_bedroom")
        occ_back_hall = self.tracker.get_occupancy("back_hall")
        
        self.assertTrue(occ_main_bathroom > 0)
        self.assertTrue(occ_main_bedroom > 0)
        self.assertTrue(occ_back_hall > 0)

    # def test_transition_probability_update(self):
    #     self.tracker.set_occupancy("main_bathroom", 1)

    #     # Bayesian update: main_bathroom to back_hall through main_bedroom
    #     self.tracker.process_event("motion_main_bathroom", True, timestamp=0)
    #     self.tracker.process_event("motion_main_bedroom", True, timestamp=1)
    #     self.tracker.process_event("motion_back_hall", True, timestamp=2)
    #     prob_main_bathroom = self.tracker.get_occupancy_probability("main_bathroom")
    #     prob_back_hall = self.tracker.get_occupancy_probability("back_hall")
    #     self.assertAlmostEqual(prob_main_bathroom, 0.70, delta=0.1)
    #     self.assertAlmostEqual(prob_back_hall, 0.95, delta=0.1)

    def test_multi_area_sensor(self):
        # Test magnetic sensor that covers multiple areas
        self.tracker.process_event("magnetic_therace", True, timestamp=0)
        entrance_prob = self.tracker.get_occupancy_probability("entrance")
        backyard_prob = self.tracker.get_occupancy_probability("backyard")
        self.assertTrue(entrance_prob > 0 or backyard_prob > 0)

    def test_indoor_outdoor_transition(self):
        # Test transition between indoor and outdoor areas
        self.tracker.process_event("motion_entrance", True, timestamp=0)
        self.tracker.process_event("magnetic_entry", True, timestamp=1)
        entrance_occ = self.tracker.get_occupancy("entrance")
        frontyard_occ = self.tracker.get_occupancy("frontyard")
        self.assertTrue(entrance_occ > 0 or frontyard_occ > 0)


if __name__ == "__main__":
    unittest.main()
