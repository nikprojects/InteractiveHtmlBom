import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("harnesshive_export", Path(__file__).parents[1] / "InteractiveHtmlBom/harnesshive.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)

class Pad:
    def __init__(self, number, signal): self.number, self.signal = number, signal
    def GetNumber(self): return self.number
    def GetNetname(self): return self.signal
class Footprint:
    def __init__(self, ref, pads): self.ref, self.pads = ref, pads
    def GetReference(self): return self.ref
    def GetValue(self): return "Connector"
    def GetFPID(self): return self
    def GetLibItemName(self): return "Header_1x02"
    def Pads(self): return self.pads

class ExportTests(unittest.TestCase):
    def test_selection_omits_unselected_components_and_fields(self):
        parts = [Footprint("J1", [Pad("1", "GND")]), Footprint("R1", [Pad("1", "OTHER")])]
        result = export.collect_connectors(parts, ["J1"], ["MPN"], {"J1": {"MPN": "ABC", "Secret": "hidden"}})
        self.assertEqual(result, [{"reference": "J1", "fields": {"MPN": "ABC"}, "pins": [{"number": "1"}]}])
    def test_duplicate_pad_numbers_are_deduplicated_and_sorted_naturally(self):
        parts = [Footprint("J1", [Pad("10", "DATA"), Pad("2", "GND"), Pad("2", "GND"), Pad("", "")])]
        result = export.collect_connectors(parts, ["J1"], ["Signals", "Value", "Footprint"], {})
        self.assertEqual([pin["number"] for pin in result[0]["pins"]], ["2", "10"])
        self.assertEqual(result[0]["pins"][0]["signal"], "GND")
        self.assertEqual(result[0]["value"], "Connector")
    def test_only_selected_signal_names_are_exported(self):
        parts = [Footprint("J1", [Pad("1", "GND"), Pad("2", "PRIVATE")])]
        result = export.collect_connectors(parts, ["J1"], ["Signals"], {}, ["GND"])
        self.assertEqual(result[0]["pins"], [{"number": "1", "signal": "GND"}, {"number": "2"}])
    def test_conflicting_signals_and_empty_selection_rejected(self):
        with self.assertRaises(ValueError): export.collect_connectors([], [], [], {})
        with self.assertRaises(ValueError): export.collect_connectors([Footprint("J1", [Pad("1", "A"), Pad("1", "B")])], ["J1"], ["Signals"], {})
    def test_drawing_removes_unselected_net_names(self):
        data = {"nets": ["GND", "SECRET"], "footprints": [{"pads": [{"net": "GND"}, {"net": "SECRET"}]}]}
        export.restrict_drawing_nets(data, [{"pins": [{"number": "1", "signal": "GND"}]}])
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertEqual(data["nets"], ["GND"])
    def test_version_and_unicode_contract_and_size_limit(self):
        device = export.build_device(" Board ", "board.kicad_pcb", "A", "<html>Board</html>", [{"reference": "J1", "fields": {"Maker": "Møller"}, "pins": []}])
        self.assertEqual(device["schemaVersion"], 1)
        self.assertEqual(device["name"], "Board")
        self.assertEqual(json.loads(export.encode_device(device)), device)
        with patch.object(export, "MAX_UPLOAD_BYTES", 10):
            with self.assertRaises(ValueError): export.encode_device(device)
    def test_upload_uses_json_api_without_auth(self):
        with patch.object(export, "urlopen") as send:
            send.return_value.__enter__.return_value.read.return_value = b'{"id":"device-id"}'
            result = export.upload_device("http://localhost:8787/", {"name": "Board"})
            self.assertEqual(result["id"], "device-id")
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, "http://localhost:8787/api/devices")
            self.assertEqual(request.method, "POST")
            self.assertFalse(request.has_header("Authorization"))
        with self.assertRaises(ValueError): export.upload_device("file:///tmp/board", {})

class PlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[1] / "InteractiveHtmlBom/harnesshive_3d.py"
        spec = importlib.util.spec_from_file_location("harnesshive_3d_test", path)
        cls.model = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.model)

    def glb(self):
        import struct
        document = {"asset": {"version": "2.0"}, "nodes": [{"name": "J1", "translation": [0.123, 0.002, 0.087]}], "scenes": [{"nodes": [0]}], "scene": 0}
        raw = json.dumps(document).encode(); raw += b" " * (-len(raw) % 4)
        binary = struct.pack("<II", 4, 0x004E4942) + b"1234"
        data = struct.pack("<III", 0x46546C67, 2, 20 + len(raw) + len(binary)) + struct.pack("<II", len(raw), 0x4E4F534A) + raw + binary
        return document, binary, data

    def connector(self, side="front", angle=37):
        return {"reference": "J1", "placement": {"sourceId": "source-id", "positionMm": [123.456789, 87.654321], "rotationDegrees": angle, "side": side, "models": []}}

    def test_anchors_preserve_geometry_bytes_and_all_original_nodes(self):
        original, binary, data = self.glb()
        result = self.model.attach_connector_anchors(data, [self.connector()])
        document, output_binary = self.model.read_glb(result)
        self.assertEqual(output_binary, binary)
        self.assertEqual(document["nodes"][0], original["nodes"][0])
        self.assertEqual(document["nodes"][1]["translation"], [0.123456789, 0, 0.087654321])
        self.assertEqual(document["scenes"][0]["nodes"], [0, 1])

    def test_rotated_back_side_anchors_keep_full_orientation(self):
        import math
        for side in ("front", "back"):
            _, _, data = self.glb()
            connector = self.connector(side, 90)
            document, _ = self.model.read_glb(self.model.attach_connector_anchors(data, [connector]))
            node = document["nodes"][-1]
            expected = [math.sqrt(.5), 0, -math.sqrt(.5), 0] if side == "back" else [0, math.sqrt(.5), 0, math.sqrt(.5)]
            for actual, value in zip(node["rotation"], expected): self.assertAlmostEqual(actual, value)
            self.assertEqual(node["extras"]["harnesshive"]["side"], side)

    def test_invalid_glb_is_rejected(self):
        with self.assertRaises(ValueError): self.model.read_glb(b"invalid")

    def test_resolves_legacy_model_variables_without_renaming_models(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "Connector.3dshapes" / "exact.step"
            model.parent.mkdir(); model.write_text("model")
            result = self.model.resolve_model_path("${KICAD6_3DMODEL_DIR}/Connector.3dshapes/exact.wrl", folder, Path(folder))
            self.assertEqual(result, str(model))
            self.assertEqual(self.model.resolve_model_path("${KISYS3DMOD}/Connector.3dshapes/exact.wrl", folder, Path(folder)), str(model))

if __name__ == "__main__": unittest.main()
