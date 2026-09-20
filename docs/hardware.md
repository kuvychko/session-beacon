# Hardware

There are two builds of the beacon. They share the display, the protocol, the daemon and
most of the enclosure, and differ only in the board and the parts of the case that hold
it. This file keeps the shared material once and gives each board its own section where
they differ.

| Build | Board | Status |
|-------|-------|--------|
| Nano ESP32 | Arduino Nano ESP32, headerless | Complete, in daily use. The reference build |
| RP2040-Zero | Waveshare RP2040-Zero | Runs the same firmware from the same source. Bench-verified; not yet lived with for a full day, see the [roadmap](roadmap.md#next-rp2040-zero-edition) |

## Parts

Shared:

- **1.8" TFT LCD, 128x160, ST7735S, 3.3 V, SPI, 8-pin.** [JESSINIE module](https://www.amazon.com/dp/B0D31BGJWF) (ASIN B0D31BGJWF), the same listing used in `env_monitoring`. Same part number, but not an identical panel: see [Panel colour order](#panel-colour-order).

One of:

- **Arduino Nano ESP32, headerless** (ABX00092). ESP32-S3, USB-C, native USB CDC. Arduino pin names (`D10`, not GPIO numbers) are used throughout. The headered SKU is ABX00083 and will not fit the enclosure; see [enclosure.md](enclosure.md#assembly-constraints).
- **Waveshare RP2040-Zero**, the plain one, without headers soldered on. RP2040, USB-C, 3.3 V logic. The RP2040-Zero-M has headers pre-soldered and will not fit the enclosure.

Power: everything runs off the board's 3.3 V pin from USB. The display draws about 30 mA including backlight, so either board's onboard regulator handles it comfortably.

## Wiring

Eight wires, no passives, no level shifting. Both boards are 3.3 V logic, like the display. The display's two power pins and six signals are the same for both builds; only the board end differs.

Order the display's header as printed on its own silkscreen. On this module it reads `GND VCC SCL SDA RES DC CS BLK` from one end, but check yours rather than counting positions.

In the enclosed build the wires are 26 AWG silicone, soldered directly to the display's bent header and to the board. See [enclosure.md](enclosure.md#assembly-constraints) for why silicone is required.

### Nano ESP32

Identical to `env_monitoring`, which is known to work with this exact display. That table is the authority; this one is copied from it.

| Display pin | Nano ESP32 pin | Notes |
|-------------|----------------|-------|
| VCC | 3V3 | **3.3 V only. 5 V will damage the module.** |
| GND | GND | |
| CS | D10 | Chip select |
| RST (RES) | D9 | Reset |
| DC (A0) | D8 | Data / command |
| SDA (MOSI) | D11 | SPI data |
| SCL (SCK) | D13 | SPI clock |
| BLK (LED) | 3V3 | Backlight, always on |

#### Finding the pins on the Nano

Every pin is labelled on the Nano's silkscreen, so trust the board over any diagram. Two things make this wiring easy:

- **D8, D9, D10, D11 are four consecutive pins** on one header, in that order. Four of the six signal wires go to one contiguous run.
- **D13 and 3V3 are both on the opposite header**, near the USB end.

A GND pin is available on both headers; use whichever is closer to your display. The display's 8 pins are in one row on a 2.54 mm header, so female-to-female jumpers work directly, or male-to-female into a breadboard.

### RP2040-Zero

All eight wires go to one edge of the board: the left edge, looking at the component side with the USB-C connector at the top. That is the edge that faces the display's header in `mid-rp2040`.

| Display pin | RP2040-Zero pin | Notes |
|-------------|-----------------|-------|
| GND | `GND` | Second pad from the top |
| VCC | `3.3V` | **3.3 V only. `5V` is the pad above `GND`, two above this one.** |
| BLK (LED) | `3.3V` | Backlight, always on. A second wire into the `3.3V` pad, or a short link to VCC at the display header |
| — | `29` | Spare |
| CS | `28` | Chip select |
| DC (A0) | `27` | Data / command |
| RST (RES) | `26` | Reset |
| SDA (MOSI) | `15` | SPI data: SPI1 TX |
| SCL (SCK) | `14` | SPI clock: SPI1 SCK |

#### Finding the pins on the RP2040-Zero

**Go by the labels printed on the board, never by position numbers.** The three power pads are printed `5V`, `GND` and `3.3V`; every other pad is printed with its bare GPIO number. Pinout diagrams, including the one in the Arduino core, number the pads by header position from 1 at `5V`, and those numbers collide with the printed ones: the pad printed `1` is GPIO 1, in the top-right corner, nowhere near the top-left `5V`. Only the printed labels are used here.

Looking at the component side with the USB-C connector at the top, the left edge reads from top to bottom:

```
5V  GND  3.3V  29  28  27  26  15  14
```

The labels then carry on counter-clockwise, across the bottom edge and up the right one.

Why these pins:

- **`14` and `15` are SPI1's clock and data**, and the Arduino core's defaults for SPI1. Hardware SPI needs no pin overrides.
- **Nothing the display uses clashes with SPI1's other defaults.** Its MISO (`12`) and SS (`13`) are on the bottom edge and stay unconnected. The firmware drives CS itself.
- **The wires never cross.** The display's `SCL SDA RES DC CS` run matches `14 15 26 27 28` going up the edge, so the five signal wires lie side by side.
- **`29` is left spare**, and so is GPIO 16, which drives the on-board RGB LED.

The firmware consequence: the display is on **SPI1**, not the default `SPI`, so the RP2040 firmware needs the constructor that takes a bus, `Adafruit_ST7735(&SPI1, TFT_CS, TFT_DC, TFT_RST)`. The three-argument one in [Display init](#display-init) would drive SPI0's pins, which are not wired.

### Before powering on

1. **VCC is on the 3.3 V pin**: `3V3` on the Nano, `3.3V` on the RP2040-Zero. Never VIN, VBUS or `5V`. This is the only mistake that destroys the display, and on the RP2040-Zero `5V` is two pads above the right one.
2. No wire is shorting to a neighbour where it meets the header or pad.
3. SDA goes to MOSI and SCL to SCK: D11 and D13 on the Nano, `15` and `14` on the RP2040-Zero. Swapping them gives a blank screen, not damage.

## Display init

Shown for the Nano ESP32, which is what `beacon.ino` builds for today. The init sequence, SPI clock, init tab and colour-order fix belong to the panel and carry over unchanged; the RP2040-Zero differs only in its pin numbers and in the constructor, which must name `&SPI1` (see [its wiring](#rp2040-zero)).

```cpp
#define TFT_CS    D10
#define TFT_RST   D9
#define TFT_DC    D8
#define TFT_MOSI  D11
#define TFT_SCLK  D13
static constexpr uint32_t SPI_HZ = 24000000;
Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_RST);   // hardware SPI

tft.setSPISpeed(SPI_HZ);     // 24 MHz
tft.initR(INITR_GREENTAB);   // required for the ST7735S variant
tft.setRotation(1);          // landscape, 160 wide x 128 high
applyPanelColorOrder();      // this panel is BGR-wired, see below
```

`TFT_MOSI` and `TFT_SCLK` are still defined because the bit-banging revert needs
them, but the 3-argument constructor above does not take them: naming them is
the library's signal to bit-bang. See below.

`INITR_GREENTAB` is not a guess to tune. `env_monitoring` established that this panel is an ST7735**S**, and the ST7735 (`BLACKTAB`) and ST7735R (`REDTAB`) gamma and offset tables produce shifted images or wrong colours on it. Bench testing confirmed the offsets and rotation are correct with `GREENTAB`.

### Hardware SPI

The firmware uses **hardware SPI**, selected by the 3-argument constructor. The
5-argument form names MOSI and SCK explicitly, which is the library's signal to
bit-bang those pins instead. `USE_HARDWARE_SPI` in `beacon.ino` switches between
them and is the whole revert.

On the Nano no rewiring was needed. The variant header for that board defines `MOSI` as `D11`
and `SCK` as `D13`, exactly where the display was already wired, so the sketch had
been bit-banging pins that can clock themselves.

Measured on the Nano ESP32, same scenes before and after:

| Case | Software SPI | Hardware SPI at 24 MHz |
|------|--------------|------------------------|
| One row's elapsed time ticking | 28 ms | about 2 ms |
| Five rows plus a moving footer bar | ~140 ms extrapolated | 11 ms |
| Full-screen repaint | 900 ms | 140 ms |

Verified on hardware: the picture is clean at 24 MHz, with no speckling or
tearing, colours correct, the attention row pulsing and the footer bar sliding
smoothly. Timings and receive counters cannot show any of that, so this needed a
human looking at the panel.

The RP2040-Zero build drives the same panel at the same 24 MHz over SPI1, and its heartbeats report 0 to 1 ms for the small repaints that a snapshot with one changed field causes, against about 2 ms on the Nano. Its full-screen repaint has not been measured separately; there is no reason to expect it to differ much, since the panel and the clock are the same.

The clock is set to 24 MHz rather than the library's 32 MHz default. An ST7735S on
jumper wires is not guaranteed at 32 MHz, and the failure is cosmetic and confusing
rather than clean: speckled pixels or a torn frame, not a blank screen. Lower
`SPI_HZ` first if the picture is ever dirty.

### Panel colour order

**This panel is BGR-wired.** With the library's defaults it renders red and blue swapped.

An earlier version of this file said `env_monitoring`'s display did not need the same fix, and called colour order a per-unit trait. That was inferred from a code comment reading "Dark blue", never from looking at the screen, and it was wrong: that panel is BGR too and had been showing high CO2 readings in blue instead of red the whole time. Both panels from this listing have been BGR, so expect BGR.

Still check a new panel rather than assuming, because the check is free: fill the screen with `0x000F` and see whether it comes out blue or red. Getting it wrong costs a warning colour that reads as a calm one.

The Adafruit library exposes no API for this, so the fix is a direct write to the Memory Access Control register with the colour-order bit cleared:

```cpp
static constexpr uint8_t CMD_MADCTL  = 0x36;
static constexpr uint8_t MADCTL_MY   = 0x80;
static constexpr uint8_t MADCTL_MV   = 0x20;
static constexpr uint8_t MADCTL_BGR  = 0x08;  // clear this bit for RGB order

// What the library writes for INITR_GREENTAB at rotation 1, minus the BGR bit.
static constexpr uint8_t MADCTL_ROT1_RGB = MADCTL_MY | MADCTL_MV;

static void applyPanelColorOrder() {
  uint8_t v = MADCTL_ROT1_RGB;
  tft.sendCommand(CMD_MADCTL, &v, 1);
}
```

`setRotation()` rewrites this register, so the override has to come after it. There is only one `setRotation()` call in the firmware; any new one needs `applyPanelColorOrder()` after it. With the override in place every `ST77XX_*` constant and every raw hex colour behaves as documented, so no other code has to know about this.

## Arduino IDE setup

Both builds use the same libraries, via Library Manager: `Adafruit GFX Library`, `Adafruit ST7735 and ST7789 Library`, and `ArduinoJson` (v7, needed by `beacon.ino` but not by the smoke test). All three support both microcontrollers.

### Nano ESP32

- Board package: **Arduino ESP32 Boards** (Arduino's own fork), board **Arduino Nano ESP32**.
- Upload over USB. The board enumerates as a COM port; during flashing it re-enumerates, so the host daemon must tolerate the port disappearing and coming back.

### RP2040-Zero

- Board package: **[arduino-pico](https://github.com/earlephilhower/arduino-pico)** (`rp2040:rp2040`), board **Waveshare RP2040 Zero**, FQBN `rp2040:rp2040:waveshare_rp2040_zero`. Add `https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json` to the board manager URLs.
- Upload over USB. `arduino-cli upload -p COMx` reboots the board into its bootloader with a 1200-baud open, then copies the `.uf2`. If the board is already in BOOTSEL, or the upload cannot find the port, copy `beacon.ino.uf2` onto the `RPI-RP2` drive by hand; that is all the upload does.
- Hold BOOT while plugging the board in to force BOOTSEL. A board with no firmware on it comes up that way already.

## Bring-up procedure

Flash `firmware/tft_smoketest/tft_smoketest.ino` before `beacon.ino`. It is written for the Nano ESP32; the RP2040-Zero needs the same pin and constructor changes as the beacon before it can run there. It needs no host software and no ArduinoJson, so it isolates wiring problems from everything else. Open the Serial Monitor at 115200 with the Newline line ending.

It applies this unit's colour-order correction at init, runs a self-test, then accepts single-letter commands so you can re-run any step or change the colour order without reflashing.

| Command | Does |
|---------|------|
| `c` | Colour chart, the diagnostic for swapped channels |
| `r` | Per-channel ramps |
| `g` | Geometry, border and corner markers |
| `t` | Text metrics |
| `l` | Mock of the real beacon layout |
| `a` | Whole self-test again |
| `n` | Revert to the library's colour-order value, which is wrong on this unit |
| `x` | Toggle the RGB/BGR colour-order bit |
| `mA0` | Set the colour-order register to a raw hex value |

What each step proves:

| Step | What you should see | What a failure means |
|------|--------------------|--------------------|
| Geometry | A white border touching all four edges, corners reading TL, TR, BL, BR clockwise from top left | A coloured band along an edge, or a cut-off row, means a wrong panel offset and so a wrong init tab. Corners out of order means a wrong rotation. |
| Text | 26 characters across one line at size 1 | Confirms the font metrics the beacon layout assumes. |
| Colour chart | Six labelled bars reading red, green, blue, cyan, magenta, yellow | Anything else is a channel permutation. See below. |
| Layout | A mock of the real beacon screen | The layout review. Judge row pitch and colours here, not after writing host software. |

If the screen stays dark through all of it but the Serial Monitor prints the banner, the board is fine and the problem is in the eight wires. If the Serial Monitor prints nothing, it is the board, the cable, or the port selection.

## Colour channel troubleshooting

Resolved for the current unit, but keep this for the next panel.

The `c` command in the smoke test draws six patches chosen so that every channel permutation produces a different-looking screen. Read the bars top to bottom and find the column that matches.

| Bar | Sent | Correct | R and B swapped | R and G swapped | G and B swapped |
|-----|------|---------|-----------------|-----------------|-----------------|
| R | `F800` | red | blue | green | red |
| G | `07E0` | green | green | red | blue |
| B | `001F` | blue | red | blue | green |
| C | `07FF` | cyan | yellow | magenta | cyan |
| M | `F81F` | magenta | magenta | cyan | yellow |
| Y | `FFE0` | yellow | cyan | yellow | magenta |

Pure red, green, and blue alone cannot distinguish these cases. The cyan, magenta, and yellow bars are what disambiguate.

Red and blue swapped is the panel colour-order flag and is fixed in the register, as described above. Both sketches now boot with the correction applied; `n` reverts to the library value to show the fault again, and `x` toggles.

The other two permutations cannot come from that flag. Red and green also occupy different bit widths in the pixel format, five bits against six, so a genuine swap of those two is not something the controller offers. If a future panel lands in one of those columns, correct the palette constants in software instead of hunting for an init sequence. That costs nothing at runtime because the beacon defines every colour in one block at the top of `beacon.ino`.

## USB serial notes

The daemon is the same program for both builds and speaks the same [protocol](protocol.md) to either. Detecting which port the board is on is the only part of it that depends on the board, and the heartbeat's `board` field says which build answered.

### Nano ESP32

- The Nano ESP32 uses the ESP32-S3's native USB, so `Serial` is USB CDC. The baud rate is nominal; 115200 is used by convention.
- USB CDC takes a moment to enumerate after reset, so the smoke test waits 1.5 s before its first print. Without that delay the banner is often lost.
- Opening the port from the host may or may not reset the board depending on DTR handling. Firmware must not depend on a reset at connect. It renders whatever arrives.
- Windows assigns a COM number per physical USB port, and it can change if the cable moves. The host daemon auto-detects by VID/PID (`0x2341:0x0070`) as a fallback to the configured port.

### RP2040-Zero

- `Serial` is USB CDC through TinyUSB, as on the Nano, but a different implementation, so the Nano's findings were re-checked rather than assumed.
- **`write()` gives up after one second** instead of blocking forever. That is the fault that froze the Nano eight times in eight days, and it cannot hang this board outright. A second is still most of a frame, so both builds queue their output and pump it; see `txLine()`/`txPump()`.
- **`Serial` as a boolean is `tud_cdc_connected()`**, the same predicate the Nano build spells out by hand, and the same one this core's `write()` and `availableForWrite()` consult. The Nano's warning against `if (Serial)` is about the ESP32 core's separate flag and does not apply here.
- **There is no `setRxBufferSize()`.** The CDC receive buffer is whatever the core compiled in. Nothing is lost when it fills: USB CDC applies back-pressure, so the host's write waits instead.
- **Opening the port at 1200 baud reboots the board into its bootloader.** That is how `arduino-cli` flashes it. The daemon opens at 115200 and never changes the baud rate, so it cannot trip this; it is the RP2040's equivalent of the DTR/RTS pattern the Nano watches for, and the same rule follows: nothing on the host may try to revive a board by fiddling with the port.
- The board enumerates as VID/PID `0x2E8A:0x0003`, the Raspberry Pi vendor and the generic RP2040 product id. Every RP2040 board shares it, so the daemon's auto-detect recognises "an RP2040", not specifically a Zero. Use `--port COMx` if another RP2040 device is plugged in.
- Windows assigns a COM number per physical USB port here too.

## Enclosure

Done for both builds, unlike `env_monitoring`, which never got past the breadboard. Three printed parts stacking to 19 mm on a 40 x 60 mm footprint, with a middle and back part cut for each board, held by four M2 x 16 socket-head screws, plus an optional stand that tilts the whole thing back 25 degrees. Source files and the full bill of materials are in [enclosure.md](enclosure.md).

One thing it changes about the wiring above: the enclosed build uses 26 AWG **silicone**-insulated stranded wire rather than the breadboard jumpers used for bring-up. Silicone has almost no memory, so it lies where it is put; PVC jumpers spring back hard enough to push the display off its seat and stop the case closing.
