# Hardware

## Parts

- **Arduino Nano ESP32** (ABX00083). ESP32-S3, USB-C, native USB CDC. Arduino pin names (`D10`, not GPIO numbers) are used throughout.
- **1.8" TFT LCD, 128x160, ST7735S, 3.3 V, SPI, 8-pin.** [JESSINIE module](https://www.amazon.com/dp/B0D31BGJWF) (ASIN B0D31BGJWF), the same listing used in `env_monitoring`. Same part number, but not an identical panel: see [Panel colour order](#panel-colour-order).

Power: everything runs off the Nano's 3V3 pin from USB. The display draws about 30 mA including backlight, so the onboard regulator handles it comfortably.

## Wiring

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

Eight wires, no passives, no level shifting. Both boards are 3.3 V logic.

### Finding the pins on the Nano

Every pin is labelled on the Nano's silkscreen, so trust the board over any diagram. Two things make this wiring easy:

- **D8, D9, D10, D11 are four consecutive pins** on one header, in that order. Four of the six signal wires go to one contiguous run.
- **D13 and 3V3 are both on the opposite header**, near the USB end.

A GND pin is available on both headers; use whichever is closer to your display. The display's 8 pins are in one row on a 2.54 mm header, so female-to-female jumpers work directly, or male-to-female into a breadboard.

Order the display's header as printed on its own silkscreen. On this module it reads `GND VCC SCL SDA RES DC CS BLK` from one end, but check yours rather than counting positions.

### Before powering on

1. **VCC is on 3V3, not VIN and not VBUS.** This is the only mistake that destroys the display.
2. No wire is shorting to a neighbour where the jumper meets the header.
3. D11 goes to SDA and D13 goes to SCL. Swapping them gives a blank screen, not damage.

## Display init

```cpp
#define TFT_CS    D10
#define TFT_RST   D9
#define TFT_DC    D8
#define TFT_MOSI  D11
#define TFT_SCLK  D13
Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);

tft.initR(INITR_GREENTAB);   // required for the ST7735S variant
tft.setRotation(1);          // landscape, 160 wide x 128 high
applyPanelColorOrder();      // this panel is BGR-wired, see below
```

`INITR_GREENTAB` is not a guess to tune. `env_monitoring` established that this panel is an ST7735**S**, and the ST7735 (`BLACKTAB`) and ST7735R (`REDTAB`) gamma and offset tables produce shifted images or wrong colours on it. Bench testing confirmed the offsets and rotation are correct with `GREENTAB`.

### Hardware SPI

The firmware uses **hardware SPI**, selected by the 3-argument constructor. The
5-argument form names MOSI and SCK explicitly, which is the library's signal to
bit-bang those pins instead. `USE_HARDWARE_SPI` in `beacon.ino` switches between
them and is the whole revert.

No rewiring was needed. The variant header for this board defines `MOSI` as `D11`
and `SCK` as `D13`, exactly where the display was already wired, so the sketch had
been bit-banging pins that can clock themselves.

Measured on this panel, same scenes before and after:

| Case | Software SPI | Hardware SPI at 24 MHz |
|------|--------------|------------------------|
| One row's elapsed time ticking | 28 ms | about 2 ms |
| Five rows plus a moving footer bar | ~140 ms extrapolated | 11 ms |
| Full-screen repaint | 900 ms | 140 ms |

Verified on hardware: the picture is clean at 24 MHz, with no speckling or
tearing, colours correct, the attention row pulsing and the footer bar sliding
smoothly. Timings and receive counters cannot show any of that, so this needed a
human looking at the panel.

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

- Board package: **Arduino ESP32 Boards** (Arduino's own fork), board **Arduino Nano ESP32**.
- Libraries via Library Manager: `Adafruit GFX Library`, `Adafruit ST7735 and ST7789 Library`, and `ArduinoJson` (v7, needed by `beacon.ino` but not by the smoke test).
- Upload over USB. The board enumerates as a COM port; during flashing it re-enumerates, so the host daemon must tolerate the port disappearing and coming back.

## Bring-up procedure

Flash `firmware/tft_smoketest/tft_smoketest.ino` before `beacon.ino`. It needs no host software and no ArduinoJson, so it isolates wiring problems from everything else. Open the Serial Monitor at 115200 with the Newline line ending.

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

- The Nano ESP32 uses the ESP32-S3's native USB, so `Serial` is USB CDC. The baud rate is nominal; 115200 is used by convention.
- USB CDC takes a moment to enumerate after reset, so the smoke test waits 1.5 s before its first print. Without that delay the banner is often lost.
- Opening the port from the host may or may not reset the board depending on DTR handling. Firmware must not depend on a reset at connect. It renders whatever arrives.
- Windows assigns a COM number per physical USB port, and it can change if the cable moves. The host daemon auto-detects by VID/PID (`0x2341:0x0070`) as a fallback to the configured port.

## Enclosure

TBD. A small angled stand so the screen faces the user is enough. The `env_monitoring` build never got past the breadboard either.
