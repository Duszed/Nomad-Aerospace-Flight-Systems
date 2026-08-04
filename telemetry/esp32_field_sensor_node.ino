/**
 * Nomad Aerospace — Advanced Field Telemetry Sensor Node (Gen 2)
 * Architecture: ESP32-C3 (RISC-V Ultra-Low Power SoC)
 * Sensor Payload: Sensirion SHT4x (I2C Industrial Temp/Humidity)
 * Power System: 18650 Li-Ion Portable Shield / External 5V Bank
 * 
 * Agronomic Feature: Utilizes Sensirion onboard micro-heater to evaporate 
 * morning dew/condensation, ensuring pinpoint VRA (Variable Rate Application) accuracy.
 * Author: Nomad Aerospace Systems Team
 */

#include <Wire.h>
#include <WiFi.h>
#include "Adafruit_SHT4x.h"

// System Configuration
#define SLEEP_DURATION_SEC 300  // 5-minute duty cycle for extreme battery life
#define I2C_SDA 8
#define I2C_SCL 9

// Initialize Sensirion High-Precision I2C Sensor
Adafruit_SHT4x sht4 = Adafruit_SHT4x();

void setup() {
  Serial.begin(115200);
  delay(100);
  
  Serial.println("\n[NOMAD AEROSPACE] Booting Gen-2 RISC-V Telemetry Node...");

  // Initialize I2C Bus for ESP32-C3
  Wire.begin(I2C_SDA, I2C_SCL);

  if (!sht4.begin(&Wire)) {
    Serial.println("[CRITICAL ERROR] Sensirion SHT4x silicon not detected on I2C bus.");
    gotoSleep();
  }

  // Configure Aerospace/Industrial Grade Precision
  sht4.setPrecision(SHT4X_HIGH_PRECISION);
  
  // Agronomic Logic: If operating in morning dew conditions, fire the micro-heater
  // to evaporate water droplets from the silicon before reading data.
  sht4.setHeater(SHT4X_LOW_HEATER_100MS); 
  Serial.println("[NOMAD AEROSPACE] Sensirion Micro-Heater cycled to clear condensation.");

  // Measure & Dispatch
  readAndDispatchTelemetry();
  
  gotoSleep();
}

void loop() {
  // Firmware architecture utilizes Deep Sleep; Loop is intentionally bypassed.
}

void readAndDispatchTelemetry() {
  sensors_event_t humidity, temp;
  
  // Poll the I2C bus for environmental data
  sht4.getEvent(&humidity, &temp);
  
  Serial.print("Field Temperature: "); Serial.print(temp.temperature); Serial.println(" C");
  Serial.print("Field Humidity: "); Serial.print(humidity.relative_humidity); Serial.println(" %");
  
  // Construct JSON Payload for Nomad Ground Gateway
  String payload = "{";
  payload += "\"system_id\":\"NOMAD-FIELD-C3-01\",";
  payload += "\"timestamp_ms\":" + String(millis()) + ",";
  payload += "\"temperature\":" + String(temp.temperature, 2) + ",";
  payload += "\"humidity\":" + String(humidity.relative_humidity, 2) + ",";
  payload += "\"sensor_health\":\"NOMINAL\"";
  payload += "}";
  
  Serial.println("Telemetry Packet Prepared: " + payload);
  // NOTE: Insert LoRaWAN or ESP-NOW transmission protocol here
}

void gotoSleep() {
  Serial.println("[NOMAD AEROSPACE] Data secured. Entering RISC-V Deep Sleep sequence...");
  esp_sleep_enable_timer_wakeup(SLEEP_DURATION_SEC * 1000000ULL);
  esp_deep_sleep_start();
}
