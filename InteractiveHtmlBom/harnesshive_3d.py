"""Coordinate-preserving KiCad GLB export; no geometry is re-centered."""
import base64
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

MAX_MODEL_BYTES = 32 * 1024 * 1024


def read_glb(data):
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("KiCad did not produce a valid GLB assembly.")
    version, length, json_length, kind = struct.unpack_from("<IIII", data, 4)
    if version != 2 or length != len(data) or kind != 0x4E4F534A:
        raise ValueError("Invalid GLB header.")
    return json.loads(data[20:20 + json_length]), data[20 + json_length:]


def attach_connector_anchors(data, connectors):
    """Reference-plane nodes retain exact PCB XY and side/rotation metadata.

    KiCad 10's OCCT writer converts Z-up millimetres to glTF Y-up metres:
    PCB (x,y) becomes glTF (x/1000, 0, y/1000). Component geometry keeps
    KiCad's original placement, stackup height, model offset and rotation.
    """
    document, binary = read_glb(data)
    nodes = document.setdefault("nodes", [])
    scene = document["scenes"][document.get("scene", 0)]
    scene_nodes = scene.setdefault("nodes", [])
    for connector in connectors:
        placement = connector["placement"]
        x, y = placement["positionMm"]
        angle = math.radians(placement["rotationDegrees"]) / 2
        c, s = math.cos(angle), math.sin(angle)
        # Rotation about glTF Y, then a bottom-side 180-degree flip about X.
        rotation = [c, 0, -s, 0] if placement["side"] == "back" else [0, s, 0, c]
        scene_nodes.append(len(nodes))
        nodes.append({"name": "HarnessHive:" + connector["reference"],
                      "translation": [x / 1000, 0, y / 1000], "rotation": rotation,
                      "extras": {"harnesshive": {"kind": "connector-pcb-plane", "reference": connector["reference"], **placement}}})
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((-len(raw)) % 4)
    result = struct.pack("<III", 0x46546C67, 2, 20 + len(raw) + len(binary))
    return result + struct.pack("<II", len(raw), 0x4E4F534A) + raw + binary


def add_placements(connectors, footprints, to_mm):
    by_ref = {footprint.GetReference(): footprint for footprint in footprints}
    for connector in connectors:
        footprint = by_ref[connector["reference"]]
        position = footprint.GetPosition()
        connector["placement"] = {
            "sourceId": footprint.m_Uuid.AsString(),
            "positionMm": [to_mm(position.x), to_mm(position.y)],
            "rotationDegrees": float(footprint.GetOrientationDegrees()),
            "side": "back" if footprint.IsFlipped() else "front",
            "models": [{"offsetMm": [model.m_Offset.x, model.m_Offset.y, model.m_Offset.z],
                        "rotationDegrees": [model.m_Rotation.x, model.m_Rotation.y, model.m_Rotation.z],
                        "scale": [model.m_Scale.x, model.m_Scale.y, model.m_Scale.z]}
                       for model in footprint.Models()],
        }
        pad_positions = {}
        for pad in footprint.Pads():
            number = str(pad.GetNumber() if hasattr(pad, "GetNumber") else pad.GetPadName())
            point = pad.GetPosition()
            pad_positions.setdefault(number, []).append([to_mm(point.x), to_mm(point.y)])
        for pin in connector["pins"]:
            pin["positionsMm"] = pad_positions[pin["number"]]


def find_cli():
    configured = os.environ.get("HARNESSHIVE_KICAD_CLI")
    if configured:
        if not os.path.isfile(configured):
            raise ValueError("The configured KiCad CLI could not be found.")
        return configured
    found = shutil.which("kicad-cli")
    if found:
        return found
    for parent in Path(sys.executable).resolve().parents:
        candidate = parent / "Contents/MacOS/kicad-cli"
        if candidate.is_file():
            return str(candidate)
    for base in [Path.home() / "Applications", Path("/Applications")]:
        candidate = base / "KiCad/KiCad.app/Contents/MacOS/kicad-cli"
        if candidate.is_file():
            return str(candidate)
    raise ValueError("KiCad CLI is required for 3D export. Set HARNESSHIVE_KICAD_CLI to its location.")


def resolve_model_path(value, project_dir, model_dir):
    value = value.replace("${KIPRJMOD}", project_dir)
    def replace(match):
        name = match.group(1)
        return os.environ.get(name) or (str(model_dir) if (re.fullmatch(r"KICAD\d+_3DMODEL_DIR", name) or name == "KISYS3DMOD") and model_dir else match.group(0))
    value = re.sub(r"\$\{([^}]+)\}", replace, value)
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = Path(project_dir) / path
    # KiCad 10 ships STEP models without legacy .wrl aliases.
    if path.suffix.lower() in (".wrl", ".wrz"):
        for extension in (".step", ".stp", ".stpz", ".STEP", ".STP"):
            if path.with_suffix(extension).is_file():
                return str(path.with_suffix(extension))
    return str(path) if path.is_file() else value


def export_board_glb(board, connectors, pcbnew):
    cli = find_cli()
    source_path = board.GetFileName()
    source_dir = os.path.dirname(source_path)
    model_dir = Path(cli).parent.parent / "SharedSupport/3dmodels"
    with tempfile.TemporaryDirectory(prefix="harnesshive-3d-") as directory:
        snapshot_path = str(Path(directory) / "board.kicad_pcb")
        # Snapshot the current in-memory board: HTML, pin data and 3D agree even
        # when there are edits since the last save. Never overwrite the source.
        if not pcbnew.SaveBoard(snapshot_path, board):
            raise ValueError("Couldn't create a board snapshot for 3D export.")
        snapshot = pcbnew.LoadBoard(snapshot_path)
        for footprint in snapshot.GetFootprints():
            models = footprint.Models()
            for index, model in enumerate(models):
                if not model.m_Filename.startswith("kicad-embed://"):
                    model.m_Filename = resolve_model_path(model.m_Filename, source_dir, model_dir)
                    models[index] = model
        pcbnew.SaveBoard(snapshot_path, snapshot)
        output = str(Path(directory) / "board.glb")
        command = [cli, "pcb", "export", "glb", "--force", "--user-origin", "0x0mm",
                   "--subst-models", "--include-pads", "--include-soldermask", "--include-tracks",
                   "--include-zones", "--include-silkscreen", "--output", output,
                   "--define-var", "KIPRJMOD=" + source_dir, snapshot_path]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if result.returncode or not Path(output).is_file():
            raise ValueError("KiCad couldn't export this 3D board. Check the board outline and assigned models.")
        # KiCad may return success while omitting component models. A selected
        # connector must never silently disappear from the assembly.
        for connector in connectors:
            reference = connector["reference"]
            if re.search(r"Could not add 3D model for " + re.escape(reference) + r"[.\s]", result.stdout + result.stderr):
                raise ValueError("The 3D model for {} is missing. Assign an available model or turn off 3D export.".format(reference))
            if not connector["placement"]["models"]:
                raise ValueError("{} has no assigned 3D model. Assign a model or turn off 3D export.".format(reference))
        raw_model = Path(output).read_bytes()
        document, _binary = read_glb(raw_model)
        for connector in connectors:
            node_indices = [index for index, node in enumerate(document.get("nodes", []))
                            if node.get("name") == connector["reference"]]
            if not node_indices:
                raise ValueError("The 3D assembly is missing connector {}.".format(connector["reference"]))
            connector["placement"]["modelNodeIndices"] = node_indices
        data = attach_connector_anchors(raw_model, connectors)
    if len(data) > MAX_MODEL_BYTES:
        raise ValueError("The board model exceeds the 32 MB model limit.")
    return {"format": "glb", "coordinateSystem": "gltf-y-up-meters", "pcbOriginMm": [0, 0],
            "byteLength": len(data), "base64": base64.b64encode(data).decode("ascii")}
