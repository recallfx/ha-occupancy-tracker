import pytest
from config_validator import validate_config


def test_valid_config():
    config = {
        'areas': {
            'room1': {'name': 'Room 1'},
            'room2': {'name': 'Room 2'},
        },
        'adjacency': {
            'room1': ['room2'],
            'room2': ['room1'],
        },
        'sensors': {
            'sensor1': {'area': 'room1', 'type': 'motion'},
            'sensor2': {'area': 'room2', 'type': 'motion'},
        }
    }
    errors = validate_config(config)
    assert len(errors) == 0


def test_unused_area():
    config = {
        'areas': {
            'room1': {'name': 'Room 1'},
            'room2': {'name': 'Room 2'},
            'unused_room': {'name': 'Unused Room'},
        },
        'adjacency': {
            'room1': ['room2'],
            'room2': ['room1'],
        },
        'sensors': {
            'sensor1': {'area': 'room1', 'type': 'motion'},
            'sensor2': {'area': 'room2', 'type': 'motion'},
        }
    }
    errors = validate_config(config)
    assert any('unused_room' in error for error in errors)


def test_undefined_area_in_adjacency():
    config = {
        'areas': {
            'room1': {'name': 'Room 1'},
        },
        'adjacency': {
            'room1': ['room2'],  # room2 is not defined
        },
        'sensors': {
            'sensor1': {'area': 'room1', 'type': 'motion'},
        }
    }
    errors = validate_config(config)
    assert any('room2' in error for error in errors)


def test_area_without_sensors():
    config = {
        'areas': {
            'room1': {'name': 'Room 1'},
            'room2': {'name': 'Room 2'},
        },
        'adjacency': {
            'room1': ['room2'],
            'room2': ['room1'],
        },
        'sensors': {
            'sensor1': {'area': 'room1', 'type': 'motion'},
        }
    }
    errors = validate_config(config)
    assert any('room2' in error for error in errors)


def test_sensor_with_undefined_area():
    config = {
        'areas': {
            'room1': {'name': 'Room 1'},
        },
        'adjacency': {
            'room1': [],
        },
        'sensors': {
            'sensor1': {'area': 'room1', 'type': 'motion'},
            'sensor2': {'area': 'undefined_room', 'type': 'motion'},
        }
    }
    errors = validate_config(config)
    assert any('undefined_room' in error for error in errors)


def test_multi_area_sensor():
    config = {
        'areas': {
            'room1': {'name': 'Room 1'},
            'room2': {'name': 'Room 2'},
        },
        'adjacency': {
            'room1': ['room2'],
            'room2': ['room1'],
        },
        'sensors': {
            'sensor1': {'area': ['room1', 'room2'], 'type': 'motion'},
        }
    }
    errors = validate_config(config)
    assert len(errors) == 0