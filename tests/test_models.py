"""Tests for the pure parsing helpers (runs without Home Assistant installed)."""

import importlib.util
import pathlib
import sys
import unittest

_PATH = pathlib.Path(__file__).parents[1] / "custom_components" / "tago_bus" / "models.py"
_spec = importlib.util.spec_from_file_location("tago_bus_models", _PATH)
models = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = models
_spec.loader.exec_module(models)


def _response(items):
    return {"response": {"header": {"resultCode": "00"}, "body": {"items": items}}}


INFO = models.RouteInfo("CAB1", "5", "일반버스", "두정역", "두정역", "0530", "2200")


class ExtractItemsTest(unittest.TestCase):
    def test_empty_string_items(self):
        self.assertEqual(models.extract_items(_response("")), [])

    def test_single_item_object(self):
        self.assertEqual(models.extract_items(_response({"item": {"a": 1}})), [{"a": 1}])

    def test_list(self):
        self.assertEqual(len(models.extract_items(_response({"item": [{}, {}]}))), 2)

    def test_missing_body(self):
        self.assertEqual(models.extract_items({}), [])


class RouteMetaTest(unittest.TestCase):
    def test_single_route_with_updown_codes(self):
        stations = [
            {"nodeord": 1, "nodenm": "두정역", "nodeid": "N1", "updowncd": 0},
            {"nodeord": 2, "nodenm": "터미널", "nodeid": "N2", "updowncd": 0},
            {"nodeord": 3, "nodenm": "시청", "nodeid": "N3", "updowncd": 1},
            {"nodeord": 4, "nodenm": "두정역", "nodeid": "N4", "updowncd": 1},
        ]
        meta = models.build_route_meta(INFO, stations, first_index=0)
        self.assertEqual(len(meta.directions), 2)
        self.assertEqual(meta.directions_by_order[2].label, "터미널 방면")
        self.assertEqual(meta.directions_by_order[3].index, 1)

    def test_split_route_without_codes(self):
        stations = [{"nodeord": i, "nodenm": f"S{i}"} for i in (2, 1, 3)]
        meta = models.build_route_meta(INFO, stations, first_index=2)
        self.assertEqual(len(meta.directions), 1)
        self.assertEqual(meta.directions[0].first_stop, "S1")
        self.assertEqual(meta.directions[0].last_stop, "S3")
        self.assertEqual(meta.directions[0].index, 2)

    def test_no_stations_falls_back_to_route_info(self):
        meta = models.build_route_meta(INFO, [], first_index=0)
        self.assertEqual(meta.directions[0].label, "두정역 방면")


class ParseVehicleTest(unittest.TestCase):
    def setUp(self):
        stations = [
            {"nodeord": "1", "nodenm": "두정역", "updowncd": "0"},
            {"nodeord": "2", "nodenm": "터미널", "updowncd": "0"},
            {"nodeord": "3", "nodenm": "시청", "updowncd": "1"},
        ]
        self.meta = models.build_route_meta(INFO, stations, 0)

    def test_parses_strings_and_next_stop(self):
        v = models.parse_vehicle(
            {
                "gpslati": "36.8",
                "gpslong": "127.15",
                "nodeord": "1",
                "nodenm": "두정역",
                "nodeid": "N1",
                "routenm": 5,
                "vehicleno": "충남71자1234",
            },
            self.meta,
        )
        self.assertEqual(v.key, "CAB1_충남71자1234")
        self.assertEqual(v.route_no, "5")
        self.assertEqual(v.next_stop, "터미널")
        self.assertEqual(v.direction.index, 0)

    def test_skips_missing_coordinates(self):
        self.assertIsNone(models.parse_vehicle({"vehicleno": "x"}, self.meta))
        self.assertIsNone(
            models.parse_vehicle({"vehicleno": "x", "gpslati": 0, "gpslong": 0}, self.meta)
        )

    def test_unknown_order_uses_first_direction(self):
        v = models.parse_vehicle(
            {"gpslati": 36.8, "gpslong": 127.1, "vehicleno": "A", "nodeord": 99}, self.meta
        )
        self.assertIsNone(v.next_stop)
        self.assertEqual(v.direction.index, 0)


if __name__ == "__main__":
    unittest.main()
