// Session Beacon firmware for Arduino Nano ESP32 + 1.8" ST7735 TFT.
//
// Reads newline-delimited JSON snapshots over USB serial (see docs/protocol.md)
// and renders them. No policy lives here: the host decides order, labels, and
// which session is featured. This sketch only draws.
//
// Board:     Arduino Nano ESP32 (Arduino ESP32 Boards package)
// Libraries: Adafruit GFX, Adafruit ST7735 and ST7789, ArduinoJson 7
//
// Serial input starting with '{' is treated as a protocol message. Anything
// else is a local command, so the display can be exercised without a host:
//
//   demo   load a canned snapshot and animate it
//   live   leave demo mode
//   ?      command list

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

// ---- Panel colour order ----
//
// This panel is wired BGR, so the library's default for INITR_GREENTAB shows
// red and blue swapped. Confirmed on the bench with the colour chart in
// tft_smoketest. Note that the otherwise identical display in env_monitoring
// is RGB, so this is a per-unit trait, not a property of the part number.
//
// setRotation() is the only thing that writes MADCTL, so overriding it once
// after setRotation() is enough. Any new setRotation() call must be followed
// by applyPanelColorOrder() again.
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

// ---- Constants ----
static constexpr const char* FW_VERSION = "0.1.0";
static constexpr uint8_t  PROTOCOL_V    = 1;
static constexpr uint32_t BAUD          = 115200;
static constexpr size_t   LINE_MAX      = 1024;
static constexpr uint32_t NO_HOST_MS    = 10000;
static constexpr uint32_t BLINK_MS      = 500;
static constexpr uint32_t HEARTBEAT_MS  = 10000;
static constexpr uint8_t  MAX_ROWS      = 6;

static constexpr int16_t W = 160, H = 128;
static constexpr int16_t HEADER_H = 16, FOOTER_H = 16, ROW_H = 16;
static constexpr int16_t ROWS_Y = HEADER_H;
static constexpr int16_t FOOTER_Y = H - FOOTER_H;

// Row geometry. The default GFX font is 6x8 px at size 1.
//
// State is carried by the dot colour and, for the one state that matters, by
// filling the whole row. That frees the 54 px a spelled-out "NEEDS YOU" used
// to need, which is what made the label and the age collide.
//
//   dot 3..9 | label 13..109 (16 chars) | gap | age right-aligned to 157
static constexpr int16_t DOT_CX     = 6;
static constexpr int16_t DOT_R      = 3;
static constexpr int16_t LABEL_X    = 13;
static constexpr uint8_t LABEL_MAX  = 16;   // 16 * 6 = 96 px
static constexpr int16_t AGE_RIGHT  = 157;  // 4-char age starts at 133
static constexpr int16_t TEXT_DY    = 4;    // centres an 8 px glyph in a 16 px row

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

// ---- Snapshot model ----
struct Session {
  char id[9];
  char label[LABEL_MAX + 1];
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

// What is currently painted, so only changed regions are redrawn. A full
// repaint over software SPI takes long enough to be visible as a sweep, and
// the host resends a snapshot every second purely to advance the timers.
struct RowView {
  bool used = false;
  char label[LABEL_MAX + 1] = "";
  char state[6] = "";
  char age[6] = "";
  bool lit = true;      // blink phase this row was drawn in
};

struct HeaderView {
  bool valid = false;
  uint8_t n = 0;
  char cost[10] = "";
};

struct FooterView {
  bool valid = false;
  int8_t ctx = -2;
  char model[9] = "";
};

RowView   shownRows[MAX_ROWS];
HeaderView shownHeader;
FooterView shownFooter;

char lineBuf[LINE_MAX + 1];
size_t lineLen = 0;
uint32_t lastMsgMs = 0;
uint32_t lastBlinkMs = 0;
uint32_t lastHbMs = 0;
bool blinkOn = true;
bool showingNoHost = false;
bool demoMode = false;

// ---- Helpers ----
static uint16_t stateColor(const char* st) {
  if (!strcmp(st, "work"))  return C_WORK;
  if (!strcmp(st, "need"))  return C_NEED;
  if (!strcmp(st, "idle"))  return C_IDLE;
  if (!strcmp(st, "stale")) return C_STALE;
  if (!strcmp(st, "end"))   return C_END;
  return C_START;
}

static bool isNeed(const char* st) { return !strcmp(st, "need"); }

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

static void printRight(int16_t rightX, int16_t y, const char* s) {
  tft.setCursor(rightX - 6 * (int16_t)strlen(s), y);
  tft.print(s);
}

static void invalidateAll() {
  shownHeader.valid = false;
  shownFooter.valid = false;
  for (uint8_t i = 0; i < MAX_ROWS; i++) shownRows[i] = RowView();
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
  invalidateAll();
}

static void drawVersionError(int got) {
  tft.fillScreen(C_BG);
  tft.setTextSize(2);
  tft.setTextColor(C_NEED);
  tft.setCursor(10, 40);
  tft.print("protocol");
  tft.setTextSize(1);
  tft.setTextColor(C_TEXT);
  tft.setCursor(10, 70);
  tft.printf("host sent v%d, fw wants v%d", got, PROTOCOL_V);
  invalidateAll();
}

static void drawHeader() {
  char cost[10] = "";
  if (snap.cost >= 0) snprintf(cost, sizeof cost, "$%.2f", snap.cost);

  if (shownHeader.valid && shownHeader.n == snap.n && !strcmp(shownHeader.cost, cost)) return;

  tft.fillRect(0, 0, W, HEADER_H, C_HEADER);
  tft.setTextSize(1);
  tft.setTextColor(C_TEXT);
  tft.setCursor(4, TEXT_DY);
  tft.print("BEACON");

  char act[16];
  snprintf(act, sizeof act, "%u active", snap.n);
  tft.setCursor(58, TEXT_DY);
  tft.print(act);

  if (cost[0]) printRight(W - 4, TEXT_DY, cost);

  shownHeader.valid = true;
  shownHeader.n = snap.n;
  copyStr(shownHeader.cost, sizeof shownHeader.cost, cost);
}

// A row that needs attention is filled edge to edge and alternates between
// red-on-black and black-on-red. Both phases stay readable, so it reads as a
// pulse rather than as text flashing in and out.
static void drawRow(uint8_t i) {
  const Session& s = snap.s[i];
  bool need = isNeed(s.state);

  char age[6];
  fmtAge(s.age, age, sizeof age);

  RowView& v = shownRows[i];
  if (v.used && !strcmp(v.label, s.label) && !strcmp(v.state, s.state) &&
      !strcmp(v.age, age) && (!need || v.lit == blinkOn)) {
    return;
  }

  int16_t y = ROWS_Y + i * ROW_H;
  uint16_t bg = C_BG, labelFg = C_TEXT, ageFg = C_MUTED, dot = stateColor(s.state);

  if (need) {
    if (blinkOn) { bg = C_NEED; labelFg = ageFg = dot = ST77XX_BLACK; }
    else         { bg = C_BG;   labelFg = ageFg = dot = C_NEED; }
  } else if (!strcmp(s.state, "stale")) {
    ageFg = C_STALE;
  } else if (!strcmp(s.state, "end")) {
    labelFg = C_MUTED;
  }

  tft.fillRect(0, y, W, ROW_H, bg);
  tft.fillCircle(DOT_CX, y + ROW_H / 2, DOT_R, dot);

  tft.setTextSize(1);
  tft.setTextColor(labelFg);
  tft.setCursor(LABEL_X, y + TEXT_DY);
  tft.print(s.label);

  tft.setTextColor(ageFg);
  printRight(AGE_RIGHT, y + TEXT_DY, age);

  v.used = true;
  copyStr(v.label, sizeof v.label, s.label);
  copyStr(v.state, sizeof v.state, s.state);
  copyStr(v.age, sizeof v.age, age);
  v.lit = blinkOn;
}

static void clearRow(uint8_t i) {
  if (!shownRows[i].used) return;
  tft.fillRect(0, ROWS_Y + i * ROW_H, W, ROW_H, C_BG);
  shownRows[i] = RowView();
}

// The featured session, chosen by the host. Carries the detail that does not
// fit in a row: how much of the context window is gone, and which model.
static void drawFooter() {
  int8_t ctx = -1;
  const char* model = "";
  if (snap.sel >= 0 && snap.sel < snap.count) {
    ctx = snap.s[snap.sel].ctx;
    model = snap.s[snap.sel].model;
  }
  if (shownFooter.valid && shownFooter.ctx == ctx && !strcmp(shownFooter.model, model)) return;

  tft.fillRect(0, FOOTER_Y, W, FOOTER_H, C_BG);
  tft.drawFastHLine(0, FOOTER_Y, W, C_GRID);
  tft.setTextSize(1);

  int16_t ty = FOOTER_Y + 5;
  if (ctx >= 0) {
    char buf[12];
    snprintf(buf, sizeof buf, "ctx %d%%", ctx);
    tft.setTextColor(C_MUTED);
    tft.setCursor(4, ty);
    tft.print(buf);

    // "ctx 100%" is 8 chars and ends at x=52, so the bar starts clear of it
    // and still leaves room for an 8-char model name on the right.
    const int16_t barX = 56, barW = 44;
    tft.drawRect(barX, ty, barW, 7, C_GRID);
    uint16_t fill = ctx > 85 ? C_NEED : (ctx > 65 ? C_STALE : C_IDLE);
    tft.fillRect(barX + 1, ty + 1, (barW - 2) * ctx / 100, 5, fill);
  }
  if (model[0]) {
    tft.setTextColor(C_MUTED);
    printRight(W - 4, ty, model);
  }

  shownFooter.valid = true;
  shownFooter.ctx = ctx;
  copyStr(shownFooter.model, sizeof shownFooter.model, model);
}

static void render() {
  if (showingNoHost) {
    tft.fillScreen(C_BG);
    showingNoHost = false;
    invalidateAll();
  }
  drawHeader();
  for (uint8_t i = 0; i < snap.count; i++) drawRow(i);
  for (uint8_t i = snap.count; i < MAX_ROWS; i++) clearRow(i);
  drawFooter();
}

// ---- Parsing ----
static bool parseMessage(const char* line) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) return false;

  const char* t = doc["t"];
  if (!t) return false;
  lastMsgMs = millis();

  if (!strcmp(t, "hello")) return true;
  if (strcmp(t, "snap")) return true;  // unknown type: alive, but nothing to draw

  int v = doc["v"] | 0;
  if (v != PROTOCOL_V) {
    drawVersionError(v);
    return true;
  }

  Snapshot ns;
  ns.valid = true;
  ns.n = doc["n"] | 0;
  ns.sel = doc["sel"] | -1;
  ns.cost = doc["cost"] | -1.0f;

  for (JsonObject o : doc["s"].as<JsonArray>()) {
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
  render();
  return true;
}

// ---- Demo mode ----
//
// Lets the layout be judged on real hardware before any host software exists.
static void demoLoad() {
  Snapshot d;
  d.valid = true;
  d.n = 4;
  d.count = 4;
  d.sel = 0;
  d.cost = 4.20f;

  struct { const char* l; const char* st; uint32_t age; int8_t ctx; const char* m; } rows[] = {
    {"env_monitoring", "need",  844, 41, "opus5"},
    {"session-beacon", "work",  126, 62, "fable5.1"},
    {"factory-dynamics", "stale", 362, -1, ""},
    {"homelab",        "idle",    7, 18, "sonnet5"},
  };
  for (uint8_t i = 0; i < 4; i++) {
    copyStr(d.s[i].id, sizeof d.s[i].id, "demo");
    copyStr(d.s[i].label, sizeof d.s[i].label, rows[i].l);
    copyStr(d.s[i].state, sizeof d.s[i].state, rows[i].st);
    copyStr(d.s[i].model, sizeof d.s[i].model, rows[i].m);
    d.s[i].age = rows[i].age;
    d.s[i].ctx = rows[i].ctx;
  }
  snap = d;
  demoMode = true;
  render();
  Serial.println("demo mode on. 'live' to leave.");
}

static void help() {
  Serial.println("send a JSON snapshot line, or: demo | live | ?");
}

static void handleCommand(const char* cmd) {
  if (!strcmp(cmd, "demo"))      demoLoad();
  else if (!strcmp(cmd, "live")) { demoMode = false; Serial.println("demo mode off"); }
  else                           help();
}

// ---- Arduino ----
void setup() {
  Serial.begin(BAUD);
  tft.initR(INITR_GREENTAB);
  tft.setRotation(1);      // landscape 160x128
  applyPanelColorOrder();  // must follow setRotation, see note above
  drawNoHost();
  showingNoHost = true;
}

void loop() {
  uint32_t now = millis();

  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) {
        lineBuf[lineLen] = 0;
        if (lineBuf[0] == '{') parseMessage(lineBuf);
        else                   handleCommand(lineBuf);
        lineLen = 0;
      }
    } else if (lineLen < LINE_MAX) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;  // overflow: drop the line
    }
  }

  // Pulse rows that need attention.
  if (now - lastBlinkMs >= BLINK_MS) {
    lastBlinkMs = now;
    blinkOn = !blinkOn;
    if (snap.valid && !showingNoHost) {
      for (uint8_t i = 0; i < snap.count; i++)
        if (isNeed(snap.s[i].state)) drawRow(i);
    }
  }

  // Demo mode advances its own timers so the layout can be watched live.
  if (demoMode && now - lastMsgMs >= 1000) {
    lastMsgMs = now;
    for (uint8_t i = 0; i < snap.count; i++) snap.s[i].age++;
    render();
  }

  if (!demoMode && !showingNoHost && now - lastMsgMs > NO_HOST_MS) {
    drawNoHost();
    showingNoHost = true;
  }

  if (now - lastHbMs >= HEARTBEAT_MS) {
    lastHbMs = now;
    Serial.printf("{\"t\":\"hb\",\"fw\":\"%s\",\"up\":%lu}\n", FW_VERSION, (unsigned long)(now / 1000));
  }
}
