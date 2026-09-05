# Enclosure

Three printed parts that sandwich the board and display, held together by four screws
through the stack. Source files are in [`enclosure/`](../enclosure) as 3MF.

| Part | File | Footprint | Depth |
|------|------|-----------|-------|
| Front | `front-v0.3MF` | 40 x 60 mm | 5.0 mm |
| Mid | `mid-v0.3MF` | 40 x 60 mm | 8.0 mm |
| Back | `back-v0.3MF` | 40 x 60 mm | 6.0 mm |

Stacked depth is 19 mm. The footprint is smaller than the bare display module, which is
56 mm on its long edge, because the panel overhangs its own PCB.

Dimensions above were measured from the mesh bounding boxes in the 3MF files, so they
are the modelled sizes rather than the printed ones. Expect the usual shrinkage and
elephant's foot on the first layer.

## Bill of materials

| Qty | Item | Notes |
|-----|------|-------|
| 1 | Arduino Nano ESP32 | ABX00083 |
| 1 | 1.8" TFT, 128x160, ST7735S, 3.3 V, SPI, 8-pin | [JESSINIE listing](https://www.amazon.com/dp/B0D31BGJWF). Expect BGR wiring; see [hardware.md](hardware.md#panel-colour-order) |
| 4 | M2 x 12 machine screws, hex head | Through the stack |
| 4 | M2 nuts | |
| 1 | USB-C cable, data and power | Nothing else connects to the outside |
| 8 | Lengths of 26 AWG hookup wire, soft insulation | Board to display |
| 3 | Printed parts | `front`, `mid`, `back` from `enclosure/` |

**The wire specification is not incidental.** Bring-up used ordinary breadboard jumper
wires, which are stiff and take up more room than the case has. Soft-insulation 26 AWG
bends into the cavity without pushing the display off its seat or straining the header
pins. If you rebuild this, do not substitute jumpers and expect the lid to close.

Wiring itself is unchanged from bring-up: eight conductors, no passives, no level
shifting. The pinout is in [hardware.md](hardware.md#wiring).

## Print settings

Not recorded. The parts were printed, fine-tuned and fitted before this file existed,
so material, layer height, orientation and supports are all unknown to the repository.
Worth filling in from the slicer project before the next print, because "v0 fitted" is
only reproducible if the settings that made it fit are written down.

## Versioning

Files are suffixed `-v0`. That is the revision that was fitted and works. If a part is
revised, add `-v1` rather than overwriting: 3MF is a binary container, so git can store
it but cannot show you what changed between two versions. Keeping the old file is the
only practical way to compare or fall back.
