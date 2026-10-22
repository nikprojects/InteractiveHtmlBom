# HarnessHive Devices plugin

This fork adds **Upload device to HarnessHive** alongside the original Interactive HTML BOM action. The original action remains available. The HarnessHive action uses the official HarnessHive logo in its toolbar/menu icon and export dialog; the original iBOM icon is unchanged.

## Install

Copy the `InteractiveHtmlBom` directory from this checkout into KiCad's scripting/plugins directory, then restart PCB Editor (or refresh plugins). On macOS with KiCad 10 the user directory is `~/Documents/KiCad/10.0/scripting/plugins/`. Open PCB Editor → Tools → External Plugins → Upload device to HarnessHive.

Tested with KiCad 10.0.7 on macOS. The 3D path requires KiCad's `kicad-cli pcb export glb` command and available component models. If CLI discovery fails, set `HARNESSHIVE_KICAD_CLI` to the executable's absolute path.

## Export or upload

1. Save and open a board in KiCad.
2. Choose a device name and the HarnessHive **server** URL. The default is `http://localhost:8787`; `HARNESSHIVE_SERVER_URL` can override it.
3. Select the components to expose as connectors. References beginning J, P, CN, or CONN are selected initially; any component can be selected.
4. Select Signals, Value, Footprint, and any custom PCB fields to include. Select individual signal names in the signal list; unchecked signals keep their pin numbers but omit their names. Schematic-only fields must first be synchronized into the PCB. Electrical pin numbers always remain; unchecked data fields are omitted from connector records and BOM columns. Pad numbers shared by multiple physical pads are deduplicated; conflicting signals are rejected.
5. Leave **Include 3D board assembly** checked for the 3D preview, or turn it off to export the board drawing and connector schematic alone.
6. Upload directly, or export a `.json` file and use **Devices → Import device** in HarnessHive.

The current in-memory board is exported through a temporary snapshot, including unsaved edits. The source board is never overwritten. Export files may be up to 64 MiB in total, with a maximum 32 MiB GLB model and 8 MiB drawing.

## Views and selection

Every device has a **Physical** interactive board drawing and a **Logical** XYFlow schematic of the selected connectors and board-net connectivity. Devices with a model also have a **3D board** view and a **Cross-probe** tab: selecting a schematic connector highlights its actual 3D component meshes; clicking the 3D connector selects its schematic symbol. Drag to orbit and use **Fit board** to restore the camera.

The drawing retains board geometry and silkscreen; field selection controls exported connector metadata/BOM columns, not text already drawn on silkscreen. Signal names outside the selected connectors are removed from drawing net metadata. 3D export includes component geometry, pads, tracks, zones, soldermask, and silkscreen, regardless of which metadata fields are checked.

## Placement and model fidelity

Version 2 exports preserve PCB footprint UUID, absolute XY in millimetres, rotation, board side, original model offsets/rotations/scales, and all physical pad locations. The GLB uses glTF Y-up metres at PCB origin `(0, 0)`; PCB `(x, y)` maps to `(x / 1000, 0, y / 1000)` for footprint reference-plane anchors. Component geometry keeps KiCad's original transforms and stackup height. Anchors represent footprint origins, not estimated connector mating faces.

The plugin appends connector anchors and component-node mappings without changing original model nodes or binary geometry. HarnessHive checks the anchor frame and mappings before storage. The browser fits the camera instead of recentering or rescaling the assembly.

KiCad's GLB exporter substitutes matching STEP/IGES models for WRL models. Legacy model directory variables and matching `.wrl` → `.step` paths are resolved in the temporary snapshot. WRL-only models are not directly imported by this implementation. Selected connectors without available geometry fail explicitly rather than disappearing silently. Other unavailable component models follow KiCad's exporter behavior. Matching KiCad's WRL appearance exactly would require a separate VRML conversion path; the current viewer uses studio lighting, corrected unspecified STEP material defaults, antialiasing, and shadows.

## Prototype API

`POST /api/devices`, `GET /api/devices`, `GET /api/devices/:id`, and `GET /api/devices/:id/model` are intentionally unauthenticated and use a shared collection separate from organization data. The model is fetched only when the 3D view opens. No deployment or authentication changes are included.

## Tests

Run the 11 export-contract tests without KiCad:

```sh
python3 -m unittest discover -s tests -p test_harnesshive.py
```

These check component/field/signal filtering, pin deduplication, upload shape, original GLB geometry preservation, rotated front/back anchors, invalid GLB rejection, and legacy model path resolution. A real KiCad 10.0.7 ECC83 demo board was also exported and cross-probed in HarnessHive's browser preview.
