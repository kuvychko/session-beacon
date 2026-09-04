// Session Beacon firmware for Arduino Nano ESP32 + 1.8" ST7735 TFT.
//
// Reads newline-delimited JSON snapshots over USB serial (see docs/protocol.md)
// and renders them. No policy lives here: the host decides order, labels, and
// which session is featured. This sketch only draws.
//
// Board:     Arduino Nano ESP32 (Arduino ESP32 Boards package)
// Libraries: Adafruit GFX, Adafruit ST7735 and ST7789, ArduinoJson 7
//
// Status: skeleton. Compiles in spirit; not yet flashed or tested.

#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <ArduinoJson.h>

// ---- Display wiring (Arduino pin names, same as env_monitoring) ----
#define TFT_CS    D10
#define TFT_RST   D9
#define TFT_DC    D8
#define TFT_MOSI  D11
#define TFT_SCLK  D13

Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);

// ---- Constants ----
static constexpr const char* FW_VERSION   = "0.1.0";
static constexpr uint8_t  PROTOCOL_V      = 1;
static constexpr uint32_t BAUD            = 115200;
static constexpr size_t   LINE_MAX        = 1024;
static constexpr uint32_t NO_HOST_MS      = 10000;
static constexpr uint32_t BLINK_MS        = 500;
static constexpr uint32_t HEARTBEAT_MS    = 10000;
static constexpr uint8_t  MAX_ROWS        = 6;

static constexpr int16_t  W = 160, H = 128;
static constexpr int16_t  HEADER_H = 16, FOOTER_H = 16, ROW_H = 16;

// ---- Colours (RGB565) ----
#define C_BG      ST77XX_BLACK
#define C_HEADER  0x000F   // dark blue
#define C_TEXT    ST77XX_WHITE
#define C_MUTED   0x7BEF   // light grey
#define C_GRID    0x4208   // dark grey
#define C_START   0x7BEF   // grey
#define C_WORK    0x04FF   // blue
#define C_NEED    ST77XX_RED
#define C_IDLE    ST77XX_GREEN
#define C_STALE   0xFD20   // amber
#define C_END     0x4208   // dim grey

// ---- Snapshot model (last thing the host sent) ----
struct Session {
  char id[9];
  char label[15];
  char state[6];
  char model[9];
  uint32_t age;
  int8_t ctx;        // -1 = unknown
};

struct Snapshot {
  bool valid = false;
  uint8_t n = 0;
  uint8_t count = 0;
  int8_t sel = -1;
  float cost = -1;
  Session s[MAX_ROWS];
};

Snapshot snap;
char lineBuf[LINE_MAX + 1];
size_t lineLen = 0;
uint32_t lastMsgMs = 0;
uint32_t lastBlinkMs = 0;
uint32_t lastHbMs = 0;
bool blinkOn = true;
bool showingNoHost = false;

// ---- Helpers ----
static uint16_t stateColor(const char* st) {
  if (!strcmp(st, "work"))  return C_WORK;
  if (!strcmp(st, "need"))  return C_NEED;
  if (!strcmp(st, "idle"))  return C_IDLE;
  if (!strcmp(st, "stale")) return C_STALE;
  if (!strcmp(st, "end"))   return C_END;
  return C_START;
}

static const char* stateText(const char* st) {
  if (!strcmp(st, "work"))  return "working";
  if (!strcmp(st, "need"))  return "NEEDS YOU";
  if (!strcmp(st, "idle"))  return "idle";
  if (!strcmp(st, "stale")) return "stale";
  if (!strcmp(st, "end"))   return "ended";
  return "starting";
}

static void fmtAge(uint32_t s, char* out, size_t n) {
  if (s < 60)        snprintf(out, n, "%lus", (unsigned long)s);
  else if (s < 3600) snprintf(out, n, "%lum", (unsigned long)(s / 60));
  else               snprintf(out, n, "%luh", (unsigned long)(s / 3600));
}

static void copyStr(char* dst, size_t n, const char* src) {
  if (!src) { dst[0] = 0; return; }
  strncpy(dst, src, n - 1);
  dst[n - 1] = 0;
}

// ---- Parsing ----
static bool parseSnapshot(const char* line) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) return false;

  const char* t = doc["t"];
  if (!t) return false;

  if (!strcmp(t, "hello")) {
    lastMsgMs = millis();
    return true;
  }
  if (strcmp(t, "snap")) return true;  // unknown type: ignore, still counts as alive

  if (doc["v"].as<int>() != PROTOCOL_V) return false;

  Snapshot ns;
  ns.valid = true;
  ns.n = doc["n"] | 0;
  ns.sel = doc["sel"] | -1;
  ns.cost = doc["cost"] | -1.0f;

  JsonArray arr = doc["s"].as<JsonArray>();
  for (JsonObject o : arr) {
    if (ns.count >= MAX_ROWS) break;
    Session& s = ns.s[ns.count++];
    copyStr(s.id, sizeof s.id, o["id"]);
    copyStr(s.label, sizeof s.label, o["l"]);
    copyStr(s.state, sizeof s.state, o["st"]);
    copyStr(s.model, sizeof s.model, o["m"]);
    s.age = o["age"] | 0;
    s.ctx = o["ctx"] | -1;
  }
  snap = ns;
  lastMsgMs = millis();
  return true;
}

// ---- Rendering ----
static void drawNoHost() {
  tft.fillScreen(C_BG);
  tft.setTextSize(2);
  tft.setTextColor(C_MUTED);
  tft.setCursor(20, 40);
  tft.print("no host");
  tft.setTextSize(1);
  tft.setCursor(20, 70);
  tft.print("waiting for beacon-host");
  tft.setCursor(20, 82);
  tft.print("fw ");
  tft.print(FW_VERSION);
}

static void drawHeader() {
  tft.fillRect(0, 0, W, HEADER_H, C_HEADER);
  tft.setTextSize(1);
  tft.setTextColor(C_TEXT);
  tft.setCursor(4, 4);
  tft.print("BEACON");

  char buf[24];
  snprintf(buf, sizeof buf, "%u active", snap.n);
  tft.setCursor(64, 4);
  tft.print(buf);

  if (snap.cost >= 0) {
    snprintf(buf, sizeof buf, "$%.2f", snap.cost);
    tft.setCursor(W - 4 - 6 * strlen(buf), 4);
    tft.print(buf);
  }
}

static void drawRow(uint8_t i) {
  const Session& s = snap.s[i];
  int16_t y = HEADER_H + i * ROW_H;
  uint16_t col = stateColor(s.state);
  bool need = !strcmp(s.state, "need");
  bool lit = !need || blinkOn;

  tft.fillRect(0, y, W, ROW_H, C_BG);
  if (lit) tft.fillCircle(6, y + 8, 3, col);

  tft.setTextSize(1);
  tft.setTextColor(need ? (lit ? C_NEED : C_MUTED) : C_TEXT);
  tft.setCursor(14, y + 4);
  tft.print(s.label);

  tft.setTextColor(lit ? col : C_MUTED);
  tft.setCursor(104, y + 4);
  tft.print(stateText(s.state));

  char age[8];
  fmtAge(s.age, age, sizeof age);
  tft.setTextColor(C_MUTED);
  tft.setCursor(W - 4 - 6 * strlen(age), y + 4);
  tft.print(age);
}

static void drawFooter() {
  int16_t y = H - FOOTER_H;
  tft.fillRect(0, y, W, FOOTER_H, C_BG);
  tft.drawFastHLine(0, y, W, C_GRID);
  if (snap.sel < 0 || snap.sel >= snap.count) return;
  const Session& s = snap.s[snap.sel];

  tft.setTextSize(1);
  tft.setTextColor(C_MUTED);
  tft.setCursor(4, y + 5);
  if (s.ctx >= 0) {
    char buf[12];
    snprintf(buf, sizeof buf, "ctx %d%%", s.ctx);
    tft.print(buf);
    int16_t barX = 52, barW = 50;
    tft.drawRect(barX, y + 5, barW, 7, C_GRID);
    uint16_t fill = s.ctx > 85 ? C_NEED : (s.ctx > 65 ? C_STALE : C_IDLE);
    tft.fillRect(barX + 1, y + 6, (barW - 2) * s.ctx / 100, 5, fill);
  }
  if (s.model[0]) {
    tft.setCursor(W - 4 - 6 * strlen(s.model), y + 5);
    tft.print(s.model);
  }
}

static void drawAll() {
  tft.fillScreen(C_BG);
  drawHeader();
  for (uint8_t i = 0; i < snap.count; i++) drawRow(i);
  drawFooter();
}

// ---- Arduino ----
void setup() {
  Serial.begin(BAUD);
  tft.initR(INITR_GREENTAB);
  tft.setRotation(1);  // landscape 160x128
  drawNoHost();
  showingNoHost = true;
}

void loop() {
  uint32_t now = millis();

  // Serial line reader
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      lineBuf[lineLen] = 0;
      if (lineLen > 0 && lineLen <= LINE_MAX && parseSnapshot(lineBuf)) {
        if (snap.valid) { drawAll(); showingNoHost = false; }
      }
      lineLen = 0;
    } else if (lineLen < LINE_MAX) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;  // overflow: drop line
    }
  }

  // Blink rows in "need" state
  if (now - lastBlinkMs >= BLINK_MS) {
    lastBlinkMs = now;
    blinkOn = !blinkOn;
    if (snap.valid && !showingNoHost) {
      for (uint8_t i = 0; i < snap.count; i++)
        if (!strcmp(snap.s[i].state, "need")) drawRow(i);
    }
  }

  // Host silence
  if (!showingNoHost && now - lastMsgMs > NO_HOST_MS) {
    drawNoHost();
    showingNoHost = true;
  }

  // Heartbeat
  if (now - lastHbMs >= HEARTBEAT_MS) {
    lastHbMs = now;
    Serial.printf("{\"t\":\"hb\",\"fw\":\"%s\",\"up\":%lu}\n", FW_VERSION, (unsigned long)(now / 1000));
  }
}
