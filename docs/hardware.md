# Hardware

## Parts

- **Arduino Nano ESP32** (ABX00083). ESP32-S3, USB-C, native USB CDC. Arduino pin names (`D10`, not GPIO numbers) are used throughout.
- **1.8" TFT LCD, 128x160, ST7735(S), 3.3 V, SPI, 8-pin.** [JESSINIE module](https://www.amazon.com/dp/B0D31BGJWF), same unit used in `env_monitoring`.

Power: everything runs off the Nano's 3V3 pin from USB. The TFT backlight draws about 20 mA; total board draw is well under 100 mA.

## Wiring

Identical to the `env_monitoring` firmware, which is known to work with this display.

| TFT pin | Nano ESP32 pin | Notes |
|---------|----------------|-------|
| GND | GND | |
| VCC | 3V3 | Module is 3.3 V. Do **not** use 5 V / VBUS. |
| SCL (SCK) | D13 | SPI clock |
| SDA (MOSI) | D11 | SPI data |
| RES (RST) | D9 | Reset |
| DC (A0) | D8 | Data / command |
| CS | D10 | Chip select |
| BL (LED) | 3V3 | Backlight always on. Move to a PWM pin (e.g. D6) later for dimming. |

## Display init (carried over from env_monitoring)

```cpp
#define TFT_CS    D10
#define TFT_RST   D9
#define TFT_DC    D8
#define TFT_MOSI  D11
#define TFT_SCLK  D13
Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);

tft.initR(INITR_GREENTAB);
tft.setRotation(1);   // landscape, 160 wide x 128 high
```

The 5-argument constructor uses **software SPI**. It was fine for the IAQ dashboard, and the beacon redraws even less often, so it is acceptable to start. If flicker or slowness appears, switch to the hardware SPI constructor `Adafruit_ST7735(TFT_CS, TFT_DC, TFT_RST)`. D11/D13 are the default hardware SPI pins on the Nano ESP32, so no rewiring is needed.

`INITR_GREENTAB` is the tab variant that gave correct colours and offsets on this module. If a different module is used and colours are swapped or the image is shifted, try `INITR_BLACKTAB` or `INITR_REDTAB`.

## Arduino IDE setup

- Board package: **Arduino ESP32 Boards** (Arduino's own fork), board **Arduino Nano ESP32**.
- Libraries via Library Manager: `Adafruit GFX Library`, `Adafruit ST7735 and ST7789 Library`, `ArduinoJson` (v7).
- Upload over USB. The board enumerates as a COM port; during flashing it re-enumerates, so the host daemon must tolerate the port disappearing and coming back.

## USB serial notes

- The Nano ESP32 uses the ESP32-S3's native USB, so `Serial` is USB CDC. Baud rate is nominal; 115200 is used by convention.
- Opening the port from the host may or may not reset the board depending on DTR handling. Firmware must not depend on a reset at connect. It simply renders whatever arrives.
- Windows assigns a COM number per physical USB port, and it can change if the cable moves. The host daemon auto-detects by VID/PID (`0x2341:0x0070`) as a fallback to the configured port.

## Enclosure

TBD. A small angled stand so the screen faces the user is enough. The `env_monitoring` build never got past the breadboard either.
