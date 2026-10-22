"""KiCad action plugin for physical + logical HarnessHive devices."""
import os
import re
import threading

import pcbnew

from .compat import get_wx
from .core import ibom
from .core.config import Config
from .ecad.kicad import PcbnewParser, InteractiveHtmlBomPlugin
from .harnesshive import encode_device, natural_key, upload_device
from .harnesshive_export import export_from_parser
from .version import version


class HarnessHivePlugin(InteractiveHtmlBomPlugin):
    def __init__(self):
        super().__init__()
        self.name = "Upload device to HarnessHive"
        self.description = "Select connectors and fields for physical and logical device views."

    def Run(self):
        wx = get_wx()
        board = pcbnew.GetBoard()
        board_path = board.GetFileName()
        if not board_path:
            wx.MessageBox("Save this board before exporting a device.", "HarnessHive")
            return
        config = Config(version, os.path.dirname(board_path))
        if hasattr(board, "GetCurrentVariant"):
            config.kicad_variant = board.GetCurrentVariant()
        parser = PcbnewParser(board_path, config, ibom.Logger(), board)
        footprints = sorted(parser.footprints, key=lambda f: natural_key(f.GetReference()))
        fields_by_ref = {f.GetReference(): parser.get_footprint_fields(f) for f in footprints}
        field_names = sorted({field for fields in fields_by_ref.values() for field in fields}
                             - {"Value", "Footprint", "Signals"})
        fields = ["Signals", "Value", "Footprint"] + field_names
        dialog = wx.Dialog(None, title="HarnessHive device", size=(620, 700))
        layout = wx.BoxSizer(wx.VERTICAL)
        def label(text):
            layout.Add(wx.StaticText(dialog, label=text), 0, wx.ALL, 8)
        label("Device name")
        name = wx.TextCtrl(dialog, value=os.path.splitext(os.path.basename(board_path))[0])
        layout.Add(name, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        label("HarnessHive server URL (no authentication for this prototype)")
        endpoint = wx.TextCtrl(dialog, value=os.environ.get("HARNESSHIVE_SERVER_URL", "http://localhost:8787"))
        layout.Add(endpoint, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        label("Components to expose as connectors — select any component")
        choices = wx.CheckListBox(dialog, choices=["{}  {}".format(f.GetReference(), f.GetValue()) for f in footprints])
        for index, footprint in enumerate(footprints):
            choices.Check(index, bool(re.match(r"^(J|P|CN|CONN)\d", footprint.GetReference(), re.I)))
        layout.Add(choices, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        label("Include data fields (unchecked fields are omitted from connector data and BOM)")
        field_choices = wx.CheckListBox(dialog, choices=fields)
        for index in range(min(3, len(fields))):
            field_choices.Check(index)
        layout.Add(field_choices, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        signal_names = sorted({str(pad.GetNetname()) for footprint in footprints
                               for pad in footprint.Pads() if pad.GetNetname()}, key=natural_key)
        label("Signal names to include (requires Signals above)")
        signal_choices = wx.CheckListBox(dialog, choices=signal_names)
        for index in range(len(signal_names)):
            signal_choices.Check(index)
        layout.Add(signal_choices, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        signal_buttons = wx.BoxSizer(wx.HORIZONTAL)
        all_signals = wx.Button(dialog, label="All signals")
        no_signals = wx.Button(dialog, label="No signals")
        def set_signals(checked):
            for index in range(len(signal_names)):
                signal_choices.Check(index, checked)
        all_signals.Bind(wx.EVT_BUTTON, lambda event: set_signals(True))
        no_signals.Bind(wx.EVT_BUTTON, lambda event: set_signals(False))
        signal_buttons.Add(all_signals, 0, wx.ALL, 4)
        signal_buttons.Add(no_signals, 0, wx.ALL, 4)
        layout.Add(signal_buttons)
        label("Physical drawings retain board geometry and silkscreen. Logical view uses selected connector data.")
        include_3d = wx.CheckBox(dialog, label="Include 3D board assembly (preserves connector locations)")
        include_3d.SetValue(True)
        layout.Add(include_3d, 0, wx.ALL, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        export_button = wx.Button(dialog, label="Export JSON…")
        upload_button = wx.Button(dialog, label="Upload device")
        cancel_button = wx.Button(dialog, id=wx.ID_CANCEL)
        buttons.Add(export_button, 0, wx.ALL, 8)
        buttons.Add(upload_button, 0, wx.ALL, 8)
        buttons.Add(cancel_button, 0, wx.ALL, 8)
        layout.Add(buttons)
        dialog.SetSizer(layout)
        uploading = [False]
        def close(event):
            if uploading[0]:
                event.Veto()
            else:
                event.Skip()
        def key(event):
            if uploading[0] and event.GetKeyCode() == wx.WXK_ESCAPE:
                return
            event.Skip()
        dialog.Bind(wx.EVT_CLOSE, close)
        dialog.Bind(wx.EVT_CHAR_HOOK, key)

        def build():
            selected = [footprints[i].GetReference() for i in choices.GetCheckedItems()]
            selected_fields = [fields[i] for i in field_choices.GetCheckedItems()]
            return export_from_parser(parser, name.GetValue(), selected, selected_fields,
                                      [signal_names[i] for i in signal_choices.GetCheckedItems()],
                                      include_3d=include_3d.GetValue())

        def export(_event):
            try:
                device = build()
                with wx.FileDialog(dialog, "Save device export", wildcard="JSON files (*.json)|*.json",
                                   defaultFile="device.harnesshive.json", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as picker:
                    if picker.ShowModal() == wx.ID_OK:
                        with open(picker.GetPath(), "wb") as stream:
                            stream.write(encode_device(device))
            except Exception as error:
                wx.MessageBox(str(error), "Couldn't export device", parent=dialog)

        def upload(_event):
            try:
                device = build()
            except Exception as error:
                wx.MessageBox(str(error), "Couldn't export device", parent=dialog)
                return
            server = endpoint.GetValue()
            uploading[0] = True
            upload_button.Disable()
            export_button.Disable()
            cancel_button.Disable()
            dialog.EnableCloseButton(False)
            upload_button.SetLabel("Uploading…")
            def finish(result=None):
                uploading[0] = False
                upload_button.Enable(); export_button.Enable(); cancel_button.Enable()
                dialog.EnableCloseButton(True)
                upload_button.SetLabel("Upload device")
                if result:
                    wx.MessageBox("Device uploaded. Open Devices in HarnessHive to inspect its physical and logical views.", "HarnessHive", parent=dialog)
                    dialog.EndModal(wx.ID_OK)
                else:
                    wx.MessageBox("Couldn't upload this device. Check the server address or export JSON and import it in HarnessHive.", "HarnessHive", parent=dialog)
            def send():
                try:
                    result = upload_device(server, device)
                    wx.CallAfter(finish, result)
                except Exception:
                    wx.CallAfter(finish)
            threading.Thread(target=send, daemon=True).start()
        export_button.Bind(wx.EVT_BUTTON, export)
        upload_button.Bind(wx.EVT_BUTTON, upload)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
