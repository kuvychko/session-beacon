// TFT smoke test and colour diagnostic for Session Beacon.
//
// Standalone sketch to verify the ST7735S wiring and colour mapping before
// touching beacon.ino. Flash this first.
//
// Board:     Arduino Nano ESP32 (Arduino ESP32 Boards package)
// Libraries: Adafruit GFX, Adafruit ST7735 and ST7789
//
// This panel is BGR-wired, so the sketch corrects the colour order at init.
// Colours should be right out of the box; n shows the uncorrected version.
//
// On boot it runs a self-test, then drops into an interactive mode. Open the
// Serial Monitor at 115200 with the Newline line ending and type a command:
//
//   c   colour chart, the diagnostic for swapped channels
//   r   per-channel ramps
//   g   geometry: border and corner markers
//   t   text metrics
//   l   mock of the real beacon layout
//   a   run the whole self-test again
//   n   revert to the library's MADCTL, which swaps red and blue here
//   x   toggle the MADCTL colour-order bit, the RGB/BGR flag
//   mA0 set MADCTL to a raw hex value, e.g. mA0 or mA8
//   ?   this list
//
// If a MADCTL experiment leaves the screen mirrored or rotated, type n to get
// back to a known-good geometry.

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

// Memory Access Control register. Bit 3 selects RGB or BGR channel order.
static constexpr uint8_t CMD_MADCTL = 0x36;
static constexpr uint8_t MADCTL_BGR = 0x08;

// What the library writes for INITR_GREENTAB at rotation 1: MY | MV | BGR.
// This panel is BGR-wired, so with the library default red and blue come out
// swapped. Clearing the BGR bit corrects it. Confirmed on the bench with the
// colour chart below. The env_monitoring display is RGB despite the same part
// number, so treat this as a per-unit trait.
static constexpr uint8_t MADCTL_LIB_ROT1 = 0xA8;
static constexpr uint8_t MADCTL_ROT1_RGB = MADCTL_LIB_ROT1 & ~MADCTL_BGR;

uint8_t madctl = MADCTL_ROT1_RGB;
bool madctlOverridden = false;

char line[64];
size_t lineLen = 0;

// ---- MADCTL control ----

static void applyMadctl(uint8_t v) {
  madctl = v;
  madctlOverridden = true;
  tft.sendCommand(CMD_MADCTL, &madctl, 1);
  Serial.printf("MADCTL = 0x%02X (%s order)\n", madctl,
                (madctl & MADCTL_BGR) ? "BGR" : "RGB");
}

// Restores the library's own value, which on this panel is the wrong one.
// Useful for showing the fault again, and as an escape hatch if a raw MADCTL
// experiment leaves the screen mirrored.
static void resetMadctl() {
  tft.setRotation(1);
  madctl = MADCTL_LIB_ROT1;
  madctlOverridden = false;
  Serial.println("MADCTL reset to library default: BGR, red and blue swapped");
}

static void madctlStatus(char* out, size_t n) {
  snprintf(out, n, "MADCTL %02X %s%s", madctl,
           (madctl & MADCTL_BGR) ? "BGR" : "RGB",
           madctlOverridden ? "" : " (dflt)");
}

// ---- 1. Colour chart: identifies which channels are swapped ----
//
// Six patches whose displayed colours differ under every channel permutation,
// so one look at the screen identifies the mapping. Compare against the table
// printed to serial.
static void testColorChart() {
  struct { uint16_t c; const char* label; } bars[] = {
    {0xF800, "R  F800"},
    {0x07E0, "G  07E0"},
    {0x001F, "B  001F"},
    {0x07FF, "C  07FF"},
    {0xF81F, "M  F81F"},
    {0xFFE0, "Y  FFE0"},
  };

  tft.fillScreen(ST77XX_BLACK);
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(4, 2);
  tft.print("COLOUR CHART");

  for (uint8_t i = 0; i < 6; i++) {
    int16_t y = 14 + i * 15;
    tft.fillRect(0, y, W, 14, bars[i].c);
    tft.setTextColor(ST77XX_BLACK);   // readable on all six patches
    tft.setCursor(4, y + 4);
    tft.print(bars[i].label);
  }

  char st[32];
  madctlStatus(st, sizeof st);
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(4, H - 10);
  tft.print(st);

  Serial.println("\nColour chart. Read the six bars top to bottom, then find");
  Serial.println("the column below that matches what you actually see.\n");
  Serial.println("  bar   sent    RGB ok    R<->B     R<->G     G<->B");
  Serial.println("  R     F800    red       blue      green     red");
  Serial.println("  G     07E0    green     green     red       blue");
  Serial.println("  B     001F    blue      red       blue      green");
  Serial.println("  C     07FF    cyan      yellow    magenta   cyan");
  Serial.println("  M     F81F    magenta   magenta   cyan      yellow");
  Serial.println("  Y     FFE0    yellow    cyan      yellow    magenta");
  Serial.println("\nR<->B is the panel colour-order flag, corrected at init on");
  Serial.println("this unit. Type n to see the uncorrected version, x to toggle.");
  Serial.println("R<->G or G<->B cannot come from MADCTL and need a palette fix.");
  Serial.printf("Current order: %s%s\n", (madctl & MADCTL_BGR) ? "BGR" : "RGB",
                (madctl & MADCTL_BGR) ? "  <- expect red and blue swapped" : "  <- expect correct colours");
}

// ---- 2. Channel ramps ----
//
// One ramp per channel in sixteen steps. Each ramp should read as a clean
// dark-to-bright sweep of its own labelled colour. Whichever ramp looks green
// is the channel the panel drives green, regardless of what we sent.
static void testRamps() {
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextSize(1);
  tft.setTextColor(ST77XX_WHITE);
  tft.setCursor(4, 2);
  tft.print("CHANNEL RAMPS");

  const char* names[] = {"sent R", "sent G", "sent B"};
  for (uint8_t ch = 0; ch < 3; ch++) {
    int16_t y = 16 + ch * 34;
    tft.setTextColor(ST77XX_WHITE);
    tft.setCursor(4, y);
    tft.print(names[ch]);
    for (uint8_t i = 0; i < 16; i++) {
      uint16_t c;
      if (ch == 0)      c = (uint16_t)((i * 2 + 1) << 11);          // 5-bit red
      else if (ch == 1) c = (uint16_t)((i * 4 + 3) << 5);           // 6-bit green
      else              c = (uint16_t)(i * 2 + 1);                  // 5-bit blue
      tft.fillRect(i * 10, y + 10, 10, 16, c);
    }
  }
  Serial.println("Ramps drawn. Report which ramp appears red, green and blue.");
}

// ---- 3. Geometry ----
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
  tft.setCursor(46, 56);
  tft.printf("%dx%d rot%d", tft.width(), tft.height(), tft.getRotation());
  Serial.printf("geometry: %dx%d rotation %d\n", tft.width(), tft.height(), tft.getRotation());
}

// ---- 4. Text metrics ----
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
}

// ---- 5. Beacon layout mock ----
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
  Serial.println("layout: beacon mock drawn. 'NEEDS YOU' must be red, 'idle' green.");
}

static void selfTest() {
  testGeometry();  delay(2000);
  testText();      delay(2000);
  testColorChart();delay(2500);
  testBeaconLayout();
}

static void help() {
  Serial.println("\ncommands: c chart | r ramps | g geometry | t text | l layout");
  Serial.println("          a self-test | n library MADCTL | x toggle RGB/BGR | mXX raw MADCTL");
}

// ---- Command handling ----

static void handleCommand(const char* cmd) {
  switch (cmd[0]) {
    case 'c': testColorChart(); break;
    case 'r': testRamps(); break;
    case 'g': testGeometry(); break;
    case 't': testText(); break;
    case 'l': testBeaconLayout(); break;
    case 'a': selfTest(); break;
    case 'n': resetMadctl(); testColorChart(); break;
    case 'x': applyMadctl(madctl ^ MADCTL_BGR); testColorChart(); break;
    case 'm': {
      uint8_t v = (uint8_t)strtoul(cmd + 1, nullptr, 16);
      applyMadctl(v);
      testColorChart();
      break;
    }
    case '?': help(); break;
    default:
      Serial.printf("unknown command: %s\n", cmd);
      help();
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // give USB CDC time to enumerate before the first print
  Serial.println("\n=== Session Beacon TFT smoke test ===");
  Serial.println("pins: CS=D10 DC=D8 RST=D9 MOSI=D11 SCLK=D13, VCC=3V3, BLK=3V3");

  tft.initR(INITR_GREENTAB);        // required for the ST7735S variant
  tft.setRotation(1);               // landscape 160x128
  applyMadctl(MADCTL_ROT1_RGB);     // this panel is BGR-wired, correct it
  Serial.println("display initialised");

  selfTest();
  help();
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) {
        line[lineLen] = 0;
        handleCommand(line);
        lineLen = 0;
      }
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}
