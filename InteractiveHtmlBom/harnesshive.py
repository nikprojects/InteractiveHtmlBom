"""HarnessHive device export contract, independent of wx and pcbnew."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def natural_key(value):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", value)]


def collect_connectors(footprints, selected_references, selected_fields,
                       fields_by_reference, selected_signals=None):
    selected = set(selected_references)
    fields = set(selected_fields)
    signals = None if selected_signals is None else set(selected_signals)
    connectors = []
    for footprint in footprints:
        reference = footprint.GetReference()
        if reference not in selected:
            continue
        pins = {}
        for pad in footprint.Pads():
            number = str(pad.GetNumber() if hasattr(pad, "GetNumber")
                         else pad.GetPadName())
            if not number:  # mechanical/non-electrical pads
                continue
            signal = str(pad.GetNetname()) if "Signals" in fields else None
            if signals is not None and signal not in signals:
                signal = None
            if number in pins and pins[number].get("signal") != signal:
                raise ValueError("Conflicting nets on {} pin {}".format(reference, number))
            pins[number] = {"number": number}
            if signal is not None:
                pins[number]["signal"] = signal
        connector = {
            "reference": reference,
            "fields": {str(key): str(value) for key, value in
                       fields_by_reference.get(reference, {}).items()
                       if key in fields and key not in ("Value", "Footprint", "Signals")},
            "pins": sorted(pins.values(), key=lambda pin: natural_key(pin["number"])),
        }
        if "Value" in fields:
            connector["value"] = str(footprint.GetValue())
        if "Footprint" in fields:
            connector["footprint"] = str(footprint.GetFPID().GetLibItemName())
        connectors.append(connector)
    if not connectors:
        raise ValueError("Select at least one component to export as a connector.")
    references = [connector["reference"] for connector in connectors]
    if len(set(references)) != len(references):
        raise ValueError("Component references must be unique.")
    return sorted(connectors, key=lambda connector: natural_key(connector["reference"]))


def restrict_drawing_nets(pcbdata, connectors):
    """Keep selected signal names only, including track and zone data."""
    signals = {pin["signal"] for connector in connectors
               for pin in connector["pins"] if pin.get("signal")}
    def visit(value):
        if isinstance(value, dict):
            if "net" in value and value["net"] not in signals:
                del value["net"]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(pcbdata)
    if "nets" in pcbdata:
        pcbdata["nets"] = sorted(signals)


def build_device(name, board_name, revision, drawing_html, connectors):
    if not name.strip() or len(name.strip()) > 160:
        raise ValueError("Enter a device name up to 160 characters.")
    return {
        "schemaVersion": 1,
        "name": name.strip(),
        "source": {"application": "KiCad", "boardName": board_name,
                   "revision": revision,
                   "exportedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
        "drawing": {"format": "interactive-html-bom", "html": drawing_html},
        "connectors": connectors,
    }


def encode_device(device):
    encoded = json.dumps(device, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_UPLOAD_BYTES:
        raise ValueError("This device exceeds the 64 MB upload limit.")
    return encoded


def upload_device(server_url, device):
    parsed = urlparse(server_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Enter a valid HarnessHive server URL.")
    request = Request(server_url.rstrip("/") + "/api/devices",
                      data=encode_device(device),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=45) as response:
        result = json.load(response)
    if not isinstance(result.get("id"), str):
        raise ValueError("The server did not confirm this device upload.")
    return result
