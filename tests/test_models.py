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


class FavoriteStopTest(unittest.TestCase):
    def setUp(self):
        stations = [
            {"nodeord": i, "nodenm": f"S{i}", "nodeid": f"N{i}", "updowncd": 0 if i <= 3 else 1,
             "gpslati": str(36.8 + i / 1000), "gpslong": "127.1"}
            for i in range(1, 7)
        ]
        self.routes = {"CAB1": models.build_route_meta(INFO, stations, 0)}

    def test_resolve(self):
        stops, unknown = models.resolve_favorites({"CAB1": 5, "NOPE": 1}, self.routes)
        self.assertEqual(list(stops), ["CAB1"])
        self.assertEqual(stops["CAB1"].key, "CAB1:5")
        self.assertAlmostEqual(stops["CAB1"].station.latitude, 36.805)
        self.assertEqual(stops["CAB1"].label, "S5 (S6 방면)")
        self.assertEqual(unknown, {"NOPE": 1})

    def test_resolve_unknown_order(self):
        stops, unknown = models.resolve_favorites({"CAB1": 99}, self.routes)
        self.assertEqual(stops, {})
        self.assertEqual(unknown, {"CAB1": 99})

    def test_stop_key_roundtrip(self):
        self.assertEqual(models.parse_stop_key(models.stop_key("CAB:1", 7)), ("CAB:1", 7))
        self.assertIsNone(models.parse_stop_key("garbage"))
        self.assertIsNone(models.parse_stop_key("CAB1:x"))

    def test_direction_segments_are_continuous(self):
        segments = models.direction_segments(self.routes["CAB1"])
        self.assertEqual([i for i, _ in segments], [0, 1])
        first, second = segments[0][1], segments[1][1]
        self.assertEqual(len(first), 4)  # S1..S3 plus S4 to join the next direction
        self.assertEqual(first[-1], second[0])
        self.assertEqual(len(second), 3)

    def test_chunk_points_overlap(self):
        pts = [(float(i), 0.0) for i in range(10)]
        chunks = models.chunk_points(pts, 4)
        self.assertEqual([len(c) for c in chunks], [4, 4, 4])
        for a, b in zip(chunks, chunks[1:]):
            self.assertEqual(a[-1], b[0])
        self.assertEqual(chunks[-1][-1], pts[-1])
        self.assertEqual(models.chunk_points(pts[:1], 4), [])

    def test_stops_until(self):
        self.assertEqual(models.stops_until(2, 5), 3)
        self.assertEqual(models.stops_until(5, 5), 0)
        self.assertIsNone(models.stops_until(6, 5))
        self.assertIsNone(models.stops_until(None, 5))

    def test_approaching_sorted_and_filtered(self):
        meta = self.routes["CAB1"]
        stop = models.resolve_favorites({"CAB1": 5}, self.routes)[0]["CAB1"]
        data = models.TagoBusData()
        for no, order in (("A", 1), ("B", 4), ("C", 6)):
            v = models.parse_vehicle(
                {"gpslati": 36.8, "gpslong": 127.1, "vehicleno": no, "nodeord": order}, meta
            )
            data.vehicles[v.key] = v
        self.assertEqual([(n, v.vehicle_no) for n, v in data.approaching(stop)], [(1, "B"), (4, "A")])

    def test_parse_arrivals_filters_route_and_sorts(self):
        items = [
            {"routeid": "CAB1", "arrtime": "400", "arrprevstationcnt": "4", "vehicletp": "저상버스"},
            {"routeid": "OTHER", "arrtime": 10, "arrprevstationcnt": 1},
            {"routeid": "CAB1", "arrtime": 59, "arrprevstationcnt": 1},
        ]
        arrivals = models.parse_arrivals(items, "CAB1")
        self.assertEqual([a.seconds for a in arrivals], [59, 400])
        self.assertEqual(arrivals[0].minutes, 0)
        self.assertEqual(arrivals[1].minutes, 6)
        self.assertEqual(arrivals[1].vehicle_type, "저상버스")


if __name__ == "__main__":
    unittest.main()
