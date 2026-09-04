// TFT smoke test for Session Beacon.
//
// Standalone sketch to verify the ST7735S wiring before touching beacon.ino.
// Flash this first. It checks, in order: board alive, colour order, panel
// offsets, rotation, text metrics, and the beacon's row layout.
//
// Board:     Arduino Nano ESP32 (Arduino ESP32 Boards package)
// Libraries: Adafruit GFX, Adafruit ST7735 and ST7789
//
// After the self-test it echoes anything you type in the Serial Monitor
// (115200 baud, Newline line ending) onto the screen, which is a cheap way to
// exercise the display path by hand.

#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>

// Arduino pin names, NOT GPIO numbers. See docs/hardware.md.
#define TFT_CS    D10
#define TFT_RST   D9
#define TFT_DC    D8
#define TFT_MOSI  D11
#define TFT_SCLK  D13

Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);

static constexpr int16_t W = 160, H = 128;

char line[128];
size_t lineLen = 0;

// ---- Test steps ----

// 1. Solid colours. Names must match what you see. If red and blue are
//    swapped, the init tab type is wrong.
static void testColors() {
  struct { uint16_t c; const char* name; } steps[] = {
    {ST77XX_RED, "RED"}, {ST77XX_GREEN, "GREEN"}, {ST77XX_BLUE, "BLUE"},
    {ST77XX_WHITE, "WHITE"}, {ST77XX_BLACK, "BLACK"},
  };
  for (auto& s : steps) {
    tft.fillScreen(s.c);
    tft.setTextSize(2);
    tft.setTextColor(s.c == ST77XX_WHITE ? ST77XX_BLACK : ST77XX_WHITE);
    tft.setCursor(10, 56);
    tft.print(s.name);
    Serial.printf("colour: %s\n", s.name);
    delay(900);
  }
}

// 2. Border and corner markers. The white border must be visible on all four
//    edges with no coloured band or cut-off row. A band means the panel offset
//    is wrong for this tab type. Corners must read TL/TR/BL/BR in order.
static void testGeometry() {
  tft.fillScreen(ST77XX_BLACK);
  tft.drawRect(0, 0, W, H, ST77XX_WHITE);
  tft.drawRect(1, 1, W - 2, H - 2, ST77XX_WHITE);

  tft.setTextSize(1);
  tft.setTextColor(ST77XX_YELLOW);
  tft.setCursor(4, 4);            tft.print("TL");
  tft.setCursor(W - 16, 4);       tft.print("TR");
  tft.setCursor(4, H - 12);       tft.print("BL");
  tft.setCursor(W - 16, H - 12);  tft.print("BR");

  tft.setTextColor(ST77XX_CYAN);
  tft.setCursor(50, 56);
  tft.printf("%dx%d rot%d", tft.width(), tft.height(), tft.getRotation());
  Serial.printf("geometry: %dx%d rotation %d\n", tft.width(), tft.height(), tft.getRotation());
  delay(2500);
}

// 3. Text metrics. The beacon layout assumes the default 6x8 font at size 1,
//    so a 14-char label is 84 px and 26 chars fit across 160 px.
static void testText() {
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(1);
  tft.setCursor(0, 0);
  tft.println("0123456789012345678901234567");  // 26 fit, rest wraps
  tft.setTextColor(ST77XX_GREEN);
  tft.setCursor(0, 16);
  tft.println("size1 6x8 px per char");
  tft.setTextSize(2);
  tft.setTextColor(ST77XX_YELLOW);
  tft.setCursor(0, 32);
  tft.println("size2 12x16");
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_MAGENTA);
  tft.setCursor(0, 56);
  tft.println("14 chars: session-beacon");
  Serial.println("text: check 26 chars fit on one line at size 1");
  delay(2500);
}

// 4. Mock of the real beacon layout, so the row pitch and colours can be
//    judged before any host software exists. Compare with docs/architecture.md.
static void testBeaconLayout() {
  const int16_t HEADER_H = 16, ROW_H = 16, FOOTER_H = 16;
  struct { const char* label; const char* state; const char* age; uint16_t col; } rows[] = {
    {"session-beacon", "working",   "2m",  0x04FF},
    {"env_monitoring", "NEEDS YOU", "14m", ST77XX_RED},
    {"homelab",        "idle",      "0m",  ST77XX_GREEN},
    {"factory-dyn",    "stale",     "6m",  0xFD20},
  };

  tft.fillScreen(ST77XX_BLACK);
  tft.fillRect(0, 0, W, HEADER_H, 0x000F);
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(4, 4);   tft.print("BEACON");
  tft.setCursor(64, 4);  tft.print("4 active");
  tft.setCursor(124, 4); tft.print("$4.20");

  for (uint8_t i = 0; i < 4; i++) {
    int16_t y = HEADER_H + i * ROW_H;
    tft.fillCircle(6, y + 8, 3, rows[i].col);
    tft.setTextColor(rows[i].col == ST77XX_RED ? ST77XX_RED : ST77XX_WHITE);
    tft.setCursor(14, y + 4);  tft.print(rows[i].label);
    tft.setTextColor(rows[i].col);
    tft.setCursor(104, y + 4); tft.print(rows[i].state);
    tft.setTextColor(0x7BEF);
    tft.setCursor(W - 4 - 6 * strlen(rows[i].age), y + 4); tft.print(rows[i].age);
  }

  int16_t fy = H - FOOTER_H;
  tft.drawFastHLine(0, fy, W, 0x4208);
  tft.setTextColor(0x7BEF);
  tft.setCursor(4, fy + 5);  tft.print("ctx 62%");
  tft.drawRect(52, fy + 5, 50, 7, 0x4208);
  tft.fillRect(53, fy + 6, 48 * 62 / 100, 5, ST77XX_GREEN);
  tft.setCursor(W - 4 - 6 * 8, fy + 5); tft.print("fable5.1");
  Serial.println("layout: beacon mock drawn");
}

static void showLine(const char* s) {
  tft.fillRect(0, 100, W, 28, ST77XX_BLACK);
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_CYAN);
  tft.setCursor(4, 104);
  tft.print(s);
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // give USB CDC time to enumerate before the first print
  Serial.println("\n=== Session Beacon TFT smoke test ===");
  Serial.println("pins: CS=D10 DC=D8 RST=D9 MOSI=D11 SCLK=D13, VCC=3V3, BLK=3V3");

  tft.initR(INITR_GREENTAB);  // required for the ST7735S variant
  tft.setRotation(1);         // landscape 160x128
  Serial.println("display initialised");

  testColors();
  testGeometry();
  testText();
  testBeaconLayout();

  Serial.println("self-test done. Type a line (Newline ending) to echo it on screen.");
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) {
        line[lineLen] = 0;
        Serial.printf("echo: %s\n", line);
        showLine(line);
        lineLen = 0;
      }
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}
