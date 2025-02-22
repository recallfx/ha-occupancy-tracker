# Occupancy Tracking System Design Thoughts

## System Overview
- Track occupancy between multiple areas using binary sensors (motion, door)
- No occupant identification/tracking - focus on area occupancy
- System is open (occupants can enter/leave the monitored space)
- Support multiple occupants
- Areas may not all be directly connected

## Sensor Types & Setup
- Motion sensors in each room
- Magnetic sensors on external doors/windows
- External cameras triggering motion events
- All sensors are binary (motion/no motion, open/closed)

## Core Principles
1. Area transitions require sensor events - impossible to move between areas without triggering sensors
2. Areas remain occupied until valid transition events occur
3. Motion clearing doesn't automatically mean area is vacant
4. Multiple occupants can occupy same area

## Key Algorithm Components
1. Adjacency Map
   - Define possible transitions between areas
   - Configure which areas are connected

2. State Machine Approach
   - Track valid transitions between areas
   - Consider motion clearing time (e.g., 5 seconds)
   - Handle multiple occupants through state combinations

3. Bayesian Probability
   - Use probability decay for occupancy certainty
   - Example probabilities:
     - Motion 1 → Motion 2: Area 2 = 95%, Area 1 decreases to 70%
     - Sustained motion in both: Both areas rise to 95%

## Edge Cases
1. Sleep Scenario
   - Motion stops but area still occupied
   - Require transition events to clear occupancy
   - Example: Two people enter bedroom, one leaves to another room

2. Multiple Occupant Transitions
   - Handle group movements
   - Cannot determine exact count when door left open
   - Treat transitions as whole group movements
   - Use unexpected motion to retroactively adjust occupancy

3. No-Motion States
   - Area remains occupied until valid transition
   - Use time-based verification
   - Consider adjacent area activity
