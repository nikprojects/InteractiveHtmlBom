"""Reuse InteractiveHtmlBom's real board parser and renderer for device exports."""
import os
import tempfile

from .core import ibom
from .harnesshive import build_device, collect_connectors, encode_device, restrict_drawing_nets
from .version import version


def export_from_parser(parser, name, selected_references, selected_fields, selected_signals=None, include_3d=True):
    config = parser.config
    board = parser.board
    board_path = parser.file_name
    footprints = parser.footprints
    fields_by_ref = {f.GetReference(): parser.get_footprint_fields(f) for f in footprints}
    connectors = collect_connectors(footprints, selected_references, selected_fields, fields_by_ref, selected_signals)
    config.custom_metadata = ""
    config.include_nets = "Signals" in selected_fields
    config.include_tracks = True
    config.show_fields = [f for f in selected_fields if f != "Signals"]
    config.group_fields = list(config.show_fields)
    config.extra_data_file = board_path
    config.component_blacklist = [f.GetReference() for f in footprints if f.GetReference() not in selected_references]
    config.blacklist_virtual = False
    config.open_browser = False
    ibom.log = ibom.Logger(cli=True)
    pcbdata, components = parser.parse()
    if not pcbdata:
        raise ValueError("Unable to read this board drawing.")
    pcbdata["bom"] = ibom.generate_bom(components, config)
    pcbdata["ibom_version"] = version
    restrict_drawing_nets(pcbdata, connectors)
    with tempfile.TemporaryDirectory(prefix="harnesshive-") as directory:
        config.bom_dest_dir = directory
        config.bom_name_format = "device"
        output = ibom.generate_file(os.path.dirname(board_path), os.path.basename(board_path), pcbdata, config)
        with open(output, encoding="utf-8") as stream:
            drawing = stream.read()
    device = build_device(name, os.path.basename(board_path), str(board.GetTitleBlock().GetRevision()), drawing, connectors)
    import pcbnew
    from .harnesshive_3d import add_placements, export_board_glb
    add_placements(connectors, footprints, pcbnew.ToMM)
    device["schemaVersion"] = 2
    if include_3d:
        device["model3d"] = export_board_glb(board, connectors, pcbnew)
    encode_device(device)
    return device
