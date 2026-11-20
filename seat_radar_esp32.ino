/*
 * Smart Library Seat Management: ESP32 firmware, one chair, mmWave radar only
 *
 * Board: "ESP32 Dev Module" (no extra libraries needed)
 *
 * WIRING
 *   Radar HLK-LD2410B-P:  5V -> VIN (5V), GND -> GND, OUT -> GPIO27
 *                         (RX and TX pins are not needed for this code)
 */

#include <WiFi.h>
#include <HTTPClient.h>

// ---------- CONFIG: change these ----------
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* SERVER    = "http://192.168.1.10:5000";  // laptop running run_server.py
const int   TABLE_ID  = 1;
const int   SEAT_ID   = 1;

// ---------- Pin ----------
const int RADAR_PIN = 27;

// ---------- Timing ----------
const unsigned long PRESENT_CONFIRM_MS = 1500;   // radar must say "present" this long
const unsigned long ABSENT_CONFIRM_MS  = 10000;  // radar must say "empty" this long
const bool DEBUG_PRINT_RADAR = true;

bool seatPresent = false;     // confirmed state
bool sentPresent = false;     // last state the server received
bool changing = false;
unsigned long changeStart = 0;
unsigned long lastDebug = 0;

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("Connecting to WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("\nConnected, IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi failed, will retry");
  }
}

bool postJson(const String& path, const String& body) {
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) return false;

  HTTPClient http;
  http.begin(String(SERVER) + path);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(4000);
  int code = http.POST(body);
  http.end();
  return code == 200;
}

void updateRadar() {
  bool raw = (digitalRead(RADAR_PIN) == HIGH);

  if (DEBUG_PRINT_RADAR && millis() - lastDebug >= 1000) {
    lastDebug = millis();
    Serial.printf("Radar OUT = %d | confirmed = %s\n", raw, seatPresent ? "PRESENT" : "EMPTY");
  }

  if (raw != seatPresent) {
    if (!changing) {
      changing = true;
      changeStart = millis();
    } else if (millis() - changeStart >= (raw ? PRESENT_CONFIRM_MS : ABSENT_CONFIRM_MS)) {
      seatPresent = raw;
      changing = false;
      Serial.println(seatPresent ? "Seat is now OCCUPIED" : "Seat is now EMPTY");
    }
  } else {
    changing = false;
  }
}

void syncSeat() {
  if (seatPresent == sentPresent) return;
  String body = String("{\"table\":") + TABLE_ID +
                ",\"seat\":" + SEAT_ID +
                ",\"pressed\":" + (seatPresent ? "true" : "false") + "}";
  if (postJson("/pressure", body)) {
    sentPresent = seatPresent;
  } else {
    Serial.println("Could not reach server, will retry");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(RADAR_PIN, INPUT);
  connectWiFi();
  Serial.println("One-chair radar system ready");
}

void loop() {
  updateRadar();
  syncSeat();
  delay(20);
}
