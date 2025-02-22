import yaml
from pathlib import Path
from typing import List, Set


def load_config(config_path: str | Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def validate_config(config: dict) -> List[str]:
    errors: List[str] = []
    
    # Extract all sets for validation
    areas_in_config = set(config.get('areas', {}).keys())
    areas_in_adjacency = set(config.get('adjacency', {}).keys())
    areas_from_adjacency_lists = set()
    for adjacent_areas in config.get('adjacency', {}).values():
        areas_from_adjacency_lists.update(adjacent_areas)
    
    areas_in_sensors: Set[str] = set()
    for sensor in config.get('sensors', {}).values():
        if isinstance(sensor['area'], list):
            areas_in_sensors.update(sensor['area'])
        else:
            areas_in_sensors.add(sensor['area'])
    
    # Validation checks
    # 1. Check for areas defined but not used in adjacency
    unused_areas = areas_in_config - areas_in_adjacency
    if unused_areas:
        errors.append(f"Areas defined but not used in adjacency: {', '.join(unused_areas)}")
    
    # 2. Check for areas in adjacency but not defined
    undefined_areas = areas_in_adjacency - areas_in_config
    if undefined_areas:
        errors.append(f"Areas used in adjacency but not defined: {', '.join(undefined_areas)}")
    
    # 3. Check for areas referenced in adjacency lists but not defined
    undefined_adjacent_areas = areas_from_adjacency_lists - areas_in_config
    if undefined_adjacent_areas:
        errors.append(f"Areas referenced in adjacency lists but not defined: {', '.join(undefined_adjacent_areas)}")
    
    # 4. Check for areas without any sensors
    areas_without_sensors = areas_in_config - areas_in_sensors
    if areas_without_sensors:
        errors.append(f"Areas without any sensors: {', '.join(areas_without_sensors)}")
    
    # 5. Check for sensors referencing undefined areas
    undefined_sensor_areas = areas_in_sensors - areas_in_config
    if undefined_sensor_areas:
        errors.append(f"Sensors reference undefined areas: {', '.join(undefined_sensor_areas)}")
    
    return errors


def main():
    config_path = Path(__file__).parent.parent / 'config.yaml'
    config = load_config(config_path)
    errors = validate_config(config)
    
    if errors:
        print("Configuration validation failed!")
        for error in errors:
            print(f"- {error}")
        exit(1)
    else:
        print("Configuration validation passed!")


if __name__ == '__main__':
    main()