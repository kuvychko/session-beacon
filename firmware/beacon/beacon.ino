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

// Hardware SPI. D11 and D13 are this board's MOSI and SCK, confirmed in the
// variant's pins_arduino.h, so the display is already on the pins the SPI
// peripheral drives and this is a constructor change with no rewiring.
//
// The three-argument constructor selects the peripheral; the five-argument one
// names MOSI and SCK explicitly and makes the library bit-bang them instead.
// Set USE_HARDWARE_SPI to 0 to go back, which is the whole revert.
#define USE_HARDWARE_SPI 1

// The library defaults to 32 MHz. That is optimistic for an ST7735S on jumper
// wires, and the failure is cosmetic but confusing: speckled pixels or a
// scrambled frame rather than a clean failure. 24 MHz is still roughly fifteen
// times faster than bit-banging. Lower this first if the picture is dirty.
static constexpr uint32_t SPI_HZ = 24000000;

#if USE_HARDWARE_SPI
Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_RST);
#else
Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_MOSI, TFT_SCLK, TFT_RST);
#endif

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
static constexpr size_t   LINE_BUF      = 1024;
static constexpr uint32_t NO_HOST_MS    = 10000;
static constexpr uint32_t BLINK_MS      = 500;
static constexpr uint32_t HEARTBEAT_MS  = 3000;
static constexpr uint32_t FOOTER_PAGE_MS = 4000;  // footer alternation period
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

// Every varying field is a FIXED WIDTH, drawn at a FIXED x, with opaque text.
// Adafruit_GFX fills the whole 6x8 cell when a background colour is given, so
// each glyph erases what it replaces. Nothing is cleared to background first,
// which is what removes the flash on every update. Right-aligned values are
// space-padded into their field rather than moved, so the start never shifts.
static constexpr uint8_t AGE_W    = 4;
static constexpr int16_t AGE_X    = AGE_RIGHT - 6 * AGE_W;   // 133
static constexpr uint8_t COST_W   = 8;
static constexpr int16_t COST_X   = W - 4 - 6 * COST_W;      // 108
static constexpr int16_t COUNT_X  = 52;                      // "%2u active", 9 chars
static constexpr uint8_t MODEL_W  = 8;
static constexpr int16_t MODEL_X  = W - 4 - 6 * MODEL_W;     // 108
static constexpr int16_t BAR_X    = 56;
static constexpr int16_t BAR_W    = 44;

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
#define C_ERR     0xF81F   // magenta
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
  int8_t rlH5 = -1;   // account usage, 5-hour window, percent. -1 = unknown
  int8_t rlD7 = -1;   // account usage, 7-day window, percent
  Session s[MAX_ROWS];
};

Snapshot snap;

// What is currently painted, so only changed regions are redrawn. A full
// repaint over software SPI takes long enough to be visible as a sweep, and
// the host resends a snapshot every second purely to advance the timers.
struct RowView {
  bool used = false;
  char label[LABEL_MAX + 1] = "";
  char age[AGE_W + 1] = "";
  // Colours rather than the state name: they are what actually gets drawn, and
  // they already encode both the state and the blink phase.
  uint16_t bg = 0;
  uint16_t labelFg = 0;
  uint16_t ageFg = 0;
  uint16_t dot = 0;
};

struct HeaderView {
  bool valid = false;
  uint8_t n = 0;
  char cost[10] = "";
};

struct FooterView {
  bool valid = false;
  bool hint = false;   // showing the "nothing feeds this" message
  uint8_t page = 0;    // 0 = session context, 1 = account rate limits
  int8_t ctx = -2;
  int8_t h5 = -2;
  int8_t d7 = -2;
  char model[9] = "";
};

RowView   shownRows[MAX_ROWS];
HeaderView shownHeader;
FooterView shownFooter;

char lineBuf[LINE_BUF + 1];
size_t lineLen = 0;
uint32_t lastMsgMs = 0;
uint32_t lastBlinkMs = 0;
uint32_t lastHbMs = 0;
bool blinkOn = true;
uint8_t footerPage = 0;
uint32_t lastFooterFlipMs = 0;
bool showingNoHost = false;
bool shownEmpty = false;
bool demoMode = false;

// Receive counters, reported in the heartbeat. Without these, a host that
// has gone quiet and a device that is dropping or failing to parse lines
// look identical from the outside: a screen that says 'no host'.
uint32_t rxLines = 0;    // complete lines seen
uint32_t rxBad = 0;      // lines that failed to parse as JSON
uint32_t rxDropped = 0;  // lines abandoned for exceeding the buffer

// ---- Helpers ----
static uint16_t stateColor(const char* st) {
  if (!strcmp(st, "work"))  return C_WORK;
  if (!strcmp(st, "need"))  return C_NEED;
  if (!strcmp(st, "idle"))  return C_IDLE;
  if (!strcmp(st, "stale")) return C_STALE;
  if (!strcmp(st, "err"))   return C_ERR;
  if (!strcmp(st, "end"))   return C_END;
  return C_START;
}

static bool isNeed(const char* st) { return !strcmp(st, "need"); }

// Every timer comparison goes through this. A signed difference is safe
// across the 49-day millis() rollover, and safe when a stored timestamp is
// briefly *ahead* of the reference time. Plain `now - then` is neither: it
// underflows to about 4.3 billion, which compares greater than every
// timeout and fires it immediately.
static inline bool elapsed(uint32_t now, uint32_t since, uint32_t ms) {
  return (int32_t)(now - since) >= (int32_t)ms;
}

// Right-aligned inside a fixed-width field, so "9s" and "10s" occupy the same
// cells and the shorter one blanks the cell the longer one used.
static void fmtAge(uint32_t s, char* out, size_t n) {
  char tmp[12];
  if (s < 60)        snprintf(tmp, sizeof tmp, "%lus", (unsigned long)s);
  else if (s < 3600) snprintf(tmp, sizeof tmp, "%lum", (unsigned long)(s / 60));
  else               snprintf(tmp, sizeof tmp, "%luh", (unsigned long)(s / 3600));
  snprintf(out, n, "%*s", (int)AGE_W, tmp);
}

static void copyStr(char* dst, size_t n, const char* src) {
  if (!src) { dst[0] = 0; return; }
  strncpy(dst, src, n - 1);
  dst[n - 1] = 0;
}

static void invalidateAll() {
  shownHeader.valid = false;
  shownFooter.valid = false;
  shownEmpty = false;
  for (uint8_t i = 0; i < MAX_ROWS; i++) shownRows[i] = RowView();
}

// A connected host with zero sessions used to render as an empty screen, which
// is indistinguishable from a broken setup. Say so instead: the overwhelmingly
// likely cause is that the Claude Code hooks were never installed.
static void updateEmptyNotice() {
  bool empty = (snap.count == 0);
  if (empty == shownEmpty) return;

  const int16_t y = ROWS_Y, h = FOOTER_Y - ROWS_Y;
  tft.fillRect(0, y, W, h, C_BG);
  for (uint8_t i = 0; i < MAX_ROWS; i++) shownRows[i] = RowView();

  if (empty) {
    tft.setTextSize(2);
    tft.setTextColor(C_MUTED);
    tft.setCursor(14, y + h / 2 - 16);
    tft.print("no sessions");
    tft.setTextSize(1);
    tft.setTextColor(C_GRID);
    tft.setCursor(14, y + h / 2 + 8);
    tft.print("host ok. hooks set up?");
  }
  shownEmpty = empty;
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
  char cost[COST_W + 1];
  if (snap.cost >= 0) {
    char tmp[16];
    snprintf(tmp, sizeof tmp, "$%.2f", snap.cost);
    snprintf(cost, sizeof cost, "%*s", (int)COST_W, tmp);
  } else {
    snprintf(cost, sizeof cost, "%*s", (int)COST_W, "");
  }

  const bool fresh = !shownHeader.valid;
  if (!fresh && shownHeader.n == snap.n && !strcmp(shownHeader.cost, cost)) return;

  tft.setTextSize(1);
  tft.setTextColor(C_TEXT, C_HEADER);
  if (fresh) {
    tft.fillRect(0, 0, W, HEADER_H, C_HEADER);
    tft.setCursor(4, TEXT_DY);
    tft.print("BEACON");
  }
  if (fresh || shownHeader.n != snap.n) {
    char act[16];
    snprintf(act, sizeof act, "%2u active", snap.n);
    tft.setCursor(COUNT_X, TEXT_DY);
    tft.print(act);
  }
  if (fresh || strcmp(shownHeader.cost, cost)) {
    tft.setCursor(COST_X, TEXT_DY);
    tft.print(cost);
  }

  shownHeader.valid = true;
  shownHeader.n = snap.n;
  copyStr(shownHeader.cost, sizeof shownHeader.cost, cost);
}

// A row that needs attention is filled edge to edge and alternates between
// red-on-black and black-on-red. Both phases stay readable, so it reads as a
// pulse rather than as text flashing in and out.
//
// Everything else redraws only the fields that changed, in place, over an
// opaque background. The row is cleared only when its background colour
// actually changes, which for a steady session is never.
static void drawRow(uint8_t i) {
  const Session& s = snap.s[i];
  const bool need = isNeed(s.state);

  uint16_t bg = C_BG, labelFg = C_TEXT, ageFg = C_MUTED, dot = stateColor(s.state);
  if (need) {
    if (blinkOn) { bg = C_NEED; labelFg = ageFg = dot = ST77XX_BLACK; }
    else         { bg = C_BG;   labelFg = ageFg = dot = C_NEED; }
  } else if (!strcmp(s.state, "stale")) {
    ageFg = C_STALE;
  } else if (!strcmp(s.state, "err")) {
    ageFg = C_ERR;
  } else if (!strcmp(s.state, "end")) {
    labelFg = C_MUTED;
  }

  char label[LABEL_MAX + 1], age[AGE_W + 1];
  snprintf(label, sizeof label, "%-*s", (int)LABEL_MAX, s.label);
  fmtAge(s.age, age, sizeof age);

  RowView& v = shownRows[i];
  const bool fresh = !v.used || v.bg != bg;
  const int16_t y = ROWS_Y + i * ROW_H;

  if (fresh) tft.fillRect(0, y, W, ROW_H, bg);
  if (fresh || v.dot != dot) tft.fillCircle(DOT_CX, y + ROW_H / 2, DOT_R, dot);

  tft.setTextSize(1);
  if (fresh || v.labelFg != labelFg || strcmp(v.label, label)) {
    tft.setTextColor(labelFg, bg);
    tft.setCursor(LABEL_X, y + TEXT_DY);
    tft.print(label);
  }
  if (fresh || v.ageFg != ageFg || strcmp(v.age, age)) {
    tft.setTextColor(ageFg, bg);
    tft.setCursor(AGE_X, y + TEXT_DY);
    tft.print(age);
  }

  v.used = true;
  copyStr(v.label, sizeof v.label, label);
  copyStr(v.age, sizeof v.age, age);
  v.bg = bg; v.labelFg = labelFg; v.ageFg = ageFg; v.dot = dot;
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
  const int8_t h5 = snap.rlH5, d7 = snap.rlD7;
  const bool hasCtx = (ctx >= 0 || model[0]);
  const bool hasRates = (h5 >= 0 || d7 >= 0);

  // Cost, context and account usage all reach the host through the statusline
  // hook, which is optional. Without it this strip was simply blank, which
  // reads as a broken display rather than a feature never switched on.
  const bool hint = !hasCtx && !hasRates;

  // Only alternate when both pages have something to say. One page on its own
  // just stays put, so a session with no account figures does not blink at an
  // empty second page.
  uint8_t page = 0;
  if (hint)                page = 0;
  else if (hasCtx && hasRates) page = footerPage;
  else if (hasRates)       page = 1;

  // Switching page or entering the hint repaints the whole strip. The layouts
  // occupy the same fields but not the same values, so drawing one over the
  // other would leave fragments.
  const bool fresh = !shownFooter.valid || shownFooter.hint != hint ||
                     shownFooter.page != page;
  if (!fresh && shownFooter.ctx == ctx && shownFooter.h5 == h5 &&
      shownFooter.d7 == d7 && !strcmp(shownFooter.model, model)) return;

  const int16_t ty = FOOTER_Y + 5;
  tft.setTextSize(1);
  tft.setTextColor(C_MUTED, C_BG);

  if (fresh) {
    tft.fillRect(0, FOOTER_Y, W, FOOTER_H, C_BG);
    tft.drawFastHLine(0, FOOTER_Y, W, C_GRID);
  }

  if (hint) {
    tft.setTextColor(C_GRID, C_BG);
    tft.setCursor(4, ty);
    tft.print("no statusline data");
    shownFooter.valid = true;
    shownFooter.hint = hint;
    shownFooter.page = page;
    shownFooter.ctx = ctx;
    shownFooter.h5 = h5;
    shownFooter.d7 = d7;
    copyStr(shownFooter.model, sizeof shownFooter.model, model);
    return;
  }

  // Page 1: account usage. Deliberately the same three fields in the same
  // places as page 0, so alternating does not make the strip jump.
  //
  //   "5h   92%"   bar        "7d  28%"
  //   "ctx  54%"   bar          "opus5"
  if (page == 1) {
    char buf[12];
    if (h5 >= 0) snprintf(buf, sizeof buf, "5h %3d%%", h5);
    else         snprintf(buf, sizeof buf, "%8s", "5h   --");
    tft.setCursor(4, ty);
    tft.print(buf);

    if (h5 >= 0) {
      tft.drawRect(BAR_X, ty, BAR_W, 7, C_GRID);
      const int16_t inner = BAR_W - 2;
      int16_t filled = inner * h5 / 100;
      if (filled > inner) filled = inner;
      const uint16_t fill = h5 > 85 ? C_NEED : (h5 > 65 ? C_STALE : C_IDLE);
      if (filled > 0) tft.fillRect(BAR_X + 1, ty + 1, filled, 5, fill);
      if (filled < inner)
        tft.fillRect(BAR_X + 1 + filled, ty + 1, inner - filled, 5, C_BG);
    } else {
      tft.fillRect(BAR_X, ty, BAR_W, 7, C_BG);
    }

    // Same 8-char field the model name uses on page 0: "  7d  28%".
    char right[MODEL_W + 1];
    if (d7 >= 0) snprintf(right, sizeof right, "%*s%3d%%", 4, "7d", d7);
    else         snprintf(right, sizeof right, "%*s", (int)MODEL_W, "");
    tft.setCursor(MODEL_X, ty);
    tft.print(right);

    shownFooter.valid = true;
    shownFooter.hint = hint;
    shownFooter.page = page;
    shownFooter.ctx = ctx;
    shownFooter.h5 = h5;
    shownFooter.d7 = d7;
    copyStr(shownFooter.model, sizeof shownFooter.model, model);
    return;
  }

  if (fresh || shownFooter.ctx != ctx) {
    char buf[12];
    // "ctx 100%" is 8 chars and ends at x=52, clear of the bar at 56.
    if (ctx >= 0) snprintf(buf, sizeof buf, "ctx %3d%%", ctx);
    else          snprintf(buf, sizeof buf, "%8s", "");
    tft.setCursor(4, ty);
    tft.print(buf);

    if (ctx >= 0) {
      tft.drawRect(BAR_X, ty, BAR_W, 7, C_GRID);
      const int16_t inner = BAR_W - 2;
      int16_t filled = inner * ctx / 100;
      if (filled > inner) filled = inner;
      const uint16_t fill = ctx > 85 ? C_NEED : (ctx > 65 ? C_STALE : C_IDLE);
      // Repaint both halves of the bar rather than clearing it, so it slides
      // instead of blinking.
      if (filled > 0) tft.fillRect(BAR_X + 1, ty + 1, filled, 5, fill);
      if (filled < inner)
        tft.fillRect(BAR_X + 1 + filled, ty + 1, inner - filled, 5, C_BG);
    } else {
      tft.fillRect(BAR_X, ty, BAR_W, 7, C_BG);
    }
  }

  if (fresh || strcmp(shownFooter.model, model)) {
    char m[MODEL_W + 1];
    snprintf(m, sizeof m, "%*s", (int)MODEL_W, model);
    tft.setCursor(MODEL_X, ty);
    tft.print(m);
  }

  shownFooter.valid = true;
  shownFooter.hint = hint;
  shownFooter.page = page;
  shownFooter.ctx = ctx;
  shownFooter.h5 = h5;
  shownFooter.d7 = d7;
  copyStr(shownFooter.model, sizeof shownFooter.model, model);
}

uint32_t lastRenderMs = 0;  // duration of the most recent render(), ms

static void render() {
  uint32_t t0 = millis();
  if (showingNoHost) {
    tft.fillScreen(C_BG);
    showingNoHost = false;
    invalidateAll();
  }
  drawHeader();
  updateEmptyNotice();  // must precede the rows: it repaints their whole area
  for (uint8_t i = 0; i < snap.count; i++) drawRow(i);
  for (uint8_t i = snap.count; i < MAX_ROWS; i++) clearRow(i);
  drawFooter();
  lastRenderMs = millis() - t0;
}

// ---- Parsing ----
static bool parseMessage(const char* line) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) { rxBad++; return false; }

  const char* t = doc["t"];
  if (!t) { rxBad++; return false; }
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
  ns.rlH5 = doc["rl"]["h5"] | -1;
  ns.rlD7 = doc["rl"]["d7"] | -1;

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
  d.n = 5;
  d.count = 5;
  d.sel = 0;
  d.cost = 4.20f;
  d.rlH5 = 92;
  d.rlD7 = 28;

  struct { const char* l; const char* st; uint32_t age; int8_t ctx; const char* m; } rows[] = {
    {"env_monitoring", "need",  844, 41, "opus5"},
    {"session-beacon", "work",  126, 62, "fable5.1"},
    {"factory-dynamics", "err",   362, -1, ""},
    {"bench-metrology","stale", 900, -1, ""},
    {"homelab",        "idle",    7, 18, "sonnet5"},
  };
  for (uint8_t i = 0; i < 5; i++) {
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
  Serial.setRxBufferSize(4096);  // must precede begin(); default is far smaller
  Serial.begin(BAUD);
  tft.initR(INITR_GREENTAB);
#if USE_HARDWARE_SPI
  tft.setSPISpeed(SPI_HZ);
#endif
  tft.setRotation(1);      // landscape 160x128
  applyPanelColorOrder();  // must follow setRotation, see note above
  drawNoHost();
  showingNoHost = true;
}

void loop() {
  // Read serial BEFORE taking the time. Parsing a snapshot repaints the
  // screen, which is slow, and parseMessage() stamps lastMsgMs afterwards.
  // A `now` captured up here would then be behind lastMsgMs and underflow
  // the staleness check, painting "no host" straight over the frame that
  // had just been drawn correctly.
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) {
        lineBuf[lineLen] = 0;
        rxLines++;
        if (lineBuf[0] == '{') parseMessage(lineBuf);
        else                   handleCommand(lineBuf);
        lineLen = 0;
      }
    } else if (lineLen < LINE_BUF) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;
      rxDropped++;  // line longer than the buffer; abandoned
    }
  }

  const uint32_t now = millis();

  // Pulse rows that need attention.
  if (elapsed(now, lastBlinkMs, BLINK_MS)) {
    lastBlinkMs = now;
    blinkOn = !blinkOn;
    if (snap.valid && !showingNoHost) {
      for (uint8_t i = 0; i < snap.count; i++)
        if (isNeed(snap.s[i].state)) drawRow(i);
    }
  }

  // Alternate the footer between session context and account usage.
  if (elapsed(now, lastFooterFlipMs, FOOTER_PAGE_MS)) {
    lastFooterFlipMs = now;
    footerPage ^= 1;
    if (snap.valid && !showingNoHost) drawFooter();
  }

  // Demo mode advances its own timers so the layout can be watched live.
  if (demoMode && elapsed(now, lastMsgMs, 1000)) {
    lastMsgMs = now;
    for (uint8_t i = 0; i < snap.count; i++) snap.s[i].age++;
    render();
  }

  if (!demoMode && !showingNoHost && elapsed(now, lastMsgMs, NO_HOST_MS)) {
    drawNoHost();
    showingNoHost = true;
  }

  if (elapsed(now, lastHbMs, HEARTBEAT_MS)) {
    lastHbMs = now;
    int32_t since = (int32_t)(now - lastMsgMs);
    if (since < 0) since = 0;
    Serial.printf("{\"t\":\"hb\",\"fw\":\"%s\",\"up\":%lu,\"rx\":%lu,\"bad\":%lu,\"drop\":%lu,\"since\":%ld,\"render\":%lu,\"spi\":\"%s\"}\n",
                  FW_VERSION, (unsigned long)(now / 1000),
                  (unsigned long)rxLines, (unsigned long)rxBad,
                  (unsigned long)rxDropped, (long)since,
                  (unsigned long)lastRenderMs,
#if USE_HARDWARE_SPI
                  "hw"
#else
                  "sw"
#endif
                  );
  }
}
