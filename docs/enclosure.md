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
| 8 | 26 AWG **silicone**-insulated hookup wire, stranded tinned copper | Board to display. Sold as "Flexible 26 Gauge Silicone Hook up Wire Kit, Electrical Tinned Copper Wire" |
| 3 | Printed parts | `front`, `mid`, `back` from `enclosure/` |

**The wire specification is not incidental, and silicone is the part that matters.**
Bring-up used ordinary breadboard jumper wires. They are PVC-insulated and stiff, they
hold whatever bend you last put in them, and inside a 19 mm cavity that spring-back
pushes the display off its seat and strains the header pins. The lid will not close.

Silicone-insulated wire solves it on four counts:

- **It stays limp.** Silicone has almost no memory, so the wire lies where it is put
  instead of trying to straighten out against the lid.
- **It is finely stranded.** Flexible silicone wire uses many thin strands rather than
  a solid core or a few coarse ones, which is most of where the flexibility comes from.
- **Tinned copper does not fray or tarnish.** The strands stay together when stripped
  and take solder without fuss.
- **It tolerates a soldering iron.** Silicone is good to around 200 C. PVC shrinks back
  from the heat and can leave bare conductor near the joint, which matters most on
  short leads where the iron is close to the insulation. In a sealed metal-free box the
  consequence is not dramatic, but a receded jacket next to a 3.3 V rail is still a
  thing you would rather not build in.

If you rebuild this, do not substitute jumpers and expect it to fit.

Wiring itself is unchanged from bring-up: eight conductors, no passives, no level
shifting. The pinout is in [hardware.md](hardware.md#wiring).

## Print settings

These are the settings that produced the parts that fit.

| | |
|---|---|
| Printer | Prusa i3 MK3S |
| Nozzle | 0.4 mm |
| Layer height | 0.10 mm, the stock **DETAIL** preset |
| Material | PLA |

Treat the layer height as part of the fit, not as a finish preference. A box this size
does not need 0.10 mm for strength, and these parts were fine-tuned until they fitted
at this setting, so a coarser layer is a change to the tolerances rather than just a
faster print. If a reprint comes out tight, suspect that before reaching for the model.

Orientation and supports were not recorded. Shallow plates printed flat with no
supports is the obvious reading, but nobody wrote it down, so it is a guess.

## Versioning

Files are suffixed `-v0`. That is the revision that was fitted and works. If a part is
revised, add `-v1` rather than overwriting: 3MF is a binary container, so git can store
it but cannot show you what changed between two versions. Keeping the old file is the
only practical way to compare or fall back.
