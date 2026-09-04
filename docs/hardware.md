# Hardware

## Parts

- **Arduino Nano ESP32** (ABX00083). ESP32-S3, USB-C, native USB CDC. Arduino pin names (`D10`, not GPIO numbers) are used throughout.
- **1.8" TFT LCD, 128x160, ST7735S, 3.3 V, SPI, 8-pin.** [JESSINIE module](https://www.amazon.com/dp/B0D31BGJWF) (ASIN B0D31BGJWF), the same unit used in `env_monitoring`.

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
```

`INITR_GREENTAB` is not a guess to tune. `env_monitoring` established that this panel is an ST7735**S**, and the ST7735 (`BLACKTAB`) and ST7735R (`REDTAB`) gamma and offset tables produce shifted images or wrong colours on it.

The 5-argument constructor is **software SPI**, chosen in `env_monitoring` for reliability. The beacon redraws even less often than the IAQ dashboard, so it stays. If flicker or slowness appears, the hardware SPI constructor `Adafruit_ST7735(TFT_CS, TFT_DC, TFT_RST)` is a drop-in: D11 and D13 are already the Nano ESP32's hardware SPI pins, so no rewiring is needed.

## Arduino IDE setup

- Board package: **Arduino ESP32 Boards** (Arduino's own fork), board **Arduino Nano ESP32**.
- Libraries via Library Manager: `Adafruit GFX Library`, `Adafruit ST7735 and ST7789 Library`, and `ArduinoJson` (v7, needed by `beacon.ino` but not by the smoke test).
- Upload over USB. The board enumerates as a COM port; during flashing it re-enumerates, so the host daemon must tolerate the port disappearing and coming back.

## Bring-up procedure

Flash `firmware/tft_smoketest/tft_smoketest.ino` before `beacon.ino`. It needs no host software and no ArduinoJson, so it isolates wiring problems from everything else. Open the Serial Monitor at 115200 with the Newline line ending.

It runs a self-test on boot, then accepts single-letter commands so you can re-run any step or change the panel colour order without reflashing.

| Command | Does |
|---------|------|
| `c` | Colour chart, the diagnostic for swapped channels |
| `r` | Per-channel ramps |
| `g` | Geometry, border and corner markers |
| `t` | Text metrics |
| `l` | Mock of the real beacon layout |
| `a` | Whole self-test again |
| `n` | Reset the colour-order register to the library default |
| `x` | Toggle the RGB/BGR colour-order bit |
| `mA8` | Set the colour-order register to a raw hex value |

What each step proves:

| Step | What you should see | What a failure means |
|------|--------------------|--------------------|
| Geometry | A white border touching all four edges, corners reading TL, TR, BL, BR clockwise from top left | A coloured band along an edge, or a cut-off row, means a wrong panel offset and so a wrong init tab. Corners out of order means a wrong rotation. |
| Text | 26 characters across one line at size 1 | Confirms the font metrics the beacon layout assumes. |
| Colour chart | Six labelled bars reading red, green, blue, cyan, magenta, yellow | Anything else is a channel permutation. See below. |
| Layout | A mock of the real beacon screen | The layout review. Judge row pitch and colours here, not after writing host software. |

If the screen stays dark through all of it but the Serial Monitor prints the banner, the board is fine and the problem is in the eight wires. If the Serial Monitor prints nothing, it is the board, the cable, or the port selection.

## Colour channel troubleshooting

The `c` command draws six patches chosen so that every channel permutation produces a different-looking screen. Read the bars top to bottom and find the column that matches.

| Bar | Sent | Correct | Red and blue swapped | Red and green swapped | Green and blue swapped |
|-----|------|---------|----------------------|-----------------------|------------------------|
| R | `F800` | red | blue | green | red |
| G | `07E0` | green | green | red | blue |
| B | `001F` | blue | red | blue | green |
| C | `07FF` | cyan | yellow | magenta | cyan |
| M | `F81F` | magenta | magenta | cyan | yellow |
| Y | `FFE0` | yellow | cyan | yellow | magenta |

Only the red and blue case is a panel setting. The controller's colour-order flag swaps those two channels and nothing else, so `x` fixes it and the fix belongs in `beacon.ino` as an explicit register write after `setRotation`.

The other two permutations cannot come from that flag. Red and green also occupy different bit widths in the pixel format, five bits against six, so a genuine swap of those two is not something the controller offers. If the chart lands in one of those columns, the fix is to correct the palette constants in software rather than to hunt for an init sequence. That costs nothing at runtime: the beacon defines every colour in one block at the top of `beacon.ino`.

If a colour-order experiment leaves the screen mirrored or rotated, `n` restores the library default.

## USB serial notes

- The Nano ESP32 uses the ESP32-S3's native USB, so `Serial` is USB CDC. The baud rate is nominal; 115200 is used by convention.
- USB CDC takes a moment to enumerate after reset, so the smoke test waits 1.5 s before its first print. Without that delay the banner is often lost.
- Opening the port from the host may or may not reset the board depending on DTR handling. Firmware must not depend on a reset at connect. It renders whatever arrives.
- Windows assigns a COM number per physical USB port, and it can change if the cable moves. The host daemon auto-detects by VID/PID (`0x2341:0x0070`) as a fallback to the configured port.

## Enclosure

TBD. A small angled stand so the screen faces the user is enough. The `env_monitoring` build never got past the breadboard either.
