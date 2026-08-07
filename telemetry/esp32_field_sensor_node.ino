/**
 * Nomad Aerospace - Field Telemetry Sensor Node (Gen 2, Rev C)
 * ------------------------------------------------------------
 * MCU     : ESP32-C6 (RISC-V) - see SPECS.md
 *           Selected for its 802.15.4 radio: the node is HARDWARE-READY
 *           for a Zigbee/Thread mesh upgrade with no board change.
 *           Mesh firmware is NOT implemented yet - see roadmap.
 * Sensor  : Sensirion SHT4x (I2C temp/RH, on-die micro-heater)
 * Radio   : ESP-NOW point-to-point uplink to the Nomad ground gateway
 *           (implemented and working today)
 * Power   : 18650 Li-Ion, deep-sleep duty cycle, ADC battery monitor
 *
 * Condensation handling (done correctly):
 *   The micro-heater is NOT fired before every reading - that warms the
 *   sensor die and biases temperature upward. Instead:
 *     1. Take a normal (heater-off) reading.
 *     2. If RH >= DEW_RH_THRESHOLD, condensation is likely: pulse the
 *        heater once, wait HEATER_SETTLE_MS for the die to cool,
 *        then re-read.
 *     3. Report the final reading plus a flag noting the heater cycle.
 */

#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_sleep.h>
#include "Adafruit_SHT4x.h"

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
#define NODE_ID              "NOMAD-FIELD-C6-01"
#define SLEEP_DURATION_SEC   300          // 5-minute duty cycle
// [CAL] ESP32-C6 pin map differs from C3 - confirm against your devkit
// silkscreen before wiring. Any free GPIO can be remapped here.
#define I2C_SDA              6
#define I2C_SCL              7

#define BATT_ADC_PIN         1            // must be ADC1-capable on C6
#define BATT_DIVIDER_RATIO   2.0f         // 2x 100k divider: Vbat = Vadc * 2

#define DEW_RH_THRESHOLD     95.0f        // %RH above which we suspect dew
#define HEATER_SETTLE_MS     5000         // die cool-down after heater pulse

// Ground gateway ESP-NOW MAC address - set to your receiver's STA MAC.
static uint8_t GATEWAY_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF}; // broadcast until paired

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
Adafruit_SHT4x sht4;
RTC_DATA_ATTR uint32_t bootCount = 0;     // survives deep sleep - real sequence number
volatile bool sendComplete = false;

// ---------------------------------------------------------------------------
// Setup / main flow (loop() unused: deep-sleep architecture)
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(50);
  bootCount++;
  Serial.printf("\n[NOMAD] Field node (C6) boot #%lu\n", (unsigned long)bootCount);

  Wire.begin(I2C_SDA, I2C_SCL);

  if (!sht4.begin(&Wire)) {
    Serial.println("[NOMAD] ERROR: SHT4x not found on I2C bus");
    transmitFault("SENSOR_MISSING");
    goToSleep();
  }
  sht4.setPrecision(SHT4X_HIGH_PRECISION);

  float tempC, rh;
  bool heaterCycled = readEnvironment(tempC, rh);
  float battV = readBatteryVolts();

  transmitTelemetry(tempC, rh, battV, heaterCycled);
  goToSleep();
}

void loop() { /* unused - node deep-sleeps after each cycle */ }

// ---------------------------------------------------------------------------
// Sensing
// ---------------------------------------------------------------------------
bool readEnvironment(float &tempC, float &rh) {
  sensors_event_t humidity, temp;

  // 1) Normal heater-off reading
  sht4.setHeater(SHT4X_NO_HEATER);
  sht4.getEvent(&humidity, &temp);
  tempC = temp.temperature;
  rh    = humidity.relative_humidity;

  // 2) Conditional dew-clearing cycle
  if (rh >= DEW_RH_THRESHOLD) {
    Serial.printf("[NOMAD] RH %.1f%% >= %.0f%% - heater pulse to clear dew\n",
                  rh, DEW_RH_THRESHOLD);
    sht4.setHeater(SHT4X_LOW_HEATER_1S);
    sht4.getEvent(&humidity, &temp);       // this read fires the heater pulse
    sht4.setHeater(SHT4X_NO_HEATER);
    delay(HEATER_SETTLE_MS);               // let the die return to ambient
    sht4.getEvent(&humidity, &temp);       // 3) clean re-read
    tempC = temp.temperature;
    rh    = humidity.relative_humidity;
    return true;
  }
  return false;
}

float readBatteryVolts() {
  analogReadResolution(12);
  uint32_t mv = analogReadMilliVolts(BATT_ADC_PIN);
  return (mv / 1000.0f) * BATT_DIVIDER_RATIO;
}

// ---------------------------------------------------------------------------
// ESP-NOW uplink
// ---------------------------------------------------------------------------
void onEspNowSent(const uint8_t * /*mac*/, esp_now_send_status_t status) {
  sendComplete = true;
  Serial.printf("[NOMAD] ESP-NOW send: %s\n",
                status == ESP_NOW_SEND_SUCCESS ? "OK" : "FAILED");
}

bool espNowInit() {
  WiFi.mode(WIFI_STA);
  if (esp_now_init() != ESP_OK) {
    Serial.println("[NOMAD] ERROR: ESP-NOW init failed");
    return false;
  }
  esp_now_register_send_cb(onEspNowSent);

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, GATEWAY_MAC, 6);
  peer.channel = 0;
  peer.encrypt = false;   // link-layer encryption on roadmap (see SPECS.md)
  if (esp_now_add_peer(&peer) != ESP_OK) {
    Serial.println("[NOMAD] ERROR: could not add gateway peer");
    return false;
  }
  return true;
}

void espNowSendJson(const String &json) {
  if (!espNowInit()) return;
  sendComplete = false;
  esp_now_send(GATEWAY_MAC, (const uint8_t *)json.c_str(), json.length());

  uint32_t t0 = millis();                  // used only as a local wait timer
  while (!sendComplete && millis() - t0 < 1000) delay(10);
}

void transmitTelemetry(float tempC, float rh, float battV, bool heaterCycled) {
  String json = "{";
  json += "\"node\":\"" NODE_ID "\",";
  json += "\"seq\":" + String(bootCount) + ",";
  json += "\"temp_c\":" + String(tempC, 2) + ",";
  json += "\"rh_pct\":" + String(rh, 2) + ",";
  json += "\"batt_v\":" + String(battV, 2) + ",";
  json += "\"heater_cycled\":" + String(heaterCycled ? "true" : "false");
  json += "}";

  Serial.println("[NOMAD] TX: " + json);
  espNowSendJson(json);
}

void transmitFault(const char *fault) {
  String json = "{\"node\":\"" NODE_ID "\",\"seq\":" + String(bootCount) +
                ",\"fault\":\"" + fault + "\"}";
  Serial.println("[NOMAD] TX FAULT: " + json);
  espNowSendJson(json);
}

// ---------------------------------------------------------------------------
// Power management
// ---------------------------------------------------------------------------
void goToSleep() {
  Serial.printf("[NOMAD] Deep sleep %d s\n", SLEEP_DURATION_SEC);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)SLEEP_DURATION_SEC * 1000000ULL);
  esp_deep_sleep_start();
}
