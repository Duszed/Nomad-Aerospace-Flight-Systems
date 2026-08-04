# Nomad Aerospace — Flight Systems & Telemetry Repository

![License](https://img.shields.io/badge/License-Proprietary--Open%20Architecture-blue)
![Platform](https://img.shields.io/badge/Platform-ArduPilot%20%7C%20Cube%20Orange%2B-orange)
![Hardware](https://img.shields.io/badge/Hardware-30L%20Heavy--Lift%20UAV-green)
![Language](https://img.shields.io/badge/Language-C%2B%2B%20%7C%20Python%20%7C%20Param-brightgreen)

Welcome to the official core flight system documentation and custom telemetry firmware for **Nomad Aerospace** — Central Asia's premier Deep Tech agricultural UAV platform.

---

## 🚀 System Overview

Nomad Aerospace engineers 30-liter / 60kg heavy-lift autonomous UAVs designed specifically for high-efficiency precision agriculture, crop dusting, and localized environmental telemetry across Central Asia.

### Key Hardware & Avionics Specifications
* **Flight Controller:** Hex Cube Orange+ (Triple-Redundant IMU, Vibration Isolated)
* **Firmware Base:** ArduPilot Copter (Custom Parameter Architecture)
* **Propulsion:** 4x Hobbywing X11 G2 FOC Integrated Motors (18S DC)
* **Primary Navigation:** Dual-Antenna Here4 RTK GNSS 
* **Sensor Fusion Safety Shield:**
  * **Altimetry:** Benewake TF03 Long-Range Industrial LiDAR (UART/CAN)
  * **Omnidirectional Shield:** 360° Millimeter-Wave Radar Ring (77GHz)
* **Field Edge-Nodes:** ESP32-C3 (RISC-V) with Sensirion SHT4x I2C Industrial Sensors

---

## 🏗️ Hardware Ecosystem Architecture

```mermaid
graph TD
    A[Cube Orange+ Flight Controller] -->|MAVLink via 915MHz| B[Nomad Ground Control Station]
    C[Benewake TF03 LiDAR] -->|CAN Bus| A
    D[77GHz 360 Radar] -->|CAN Bus| A
    E[ESP32-C3 Sensirion Field Node] -->|LoRa / WiFi| B
    B -->|Encrypted JSON| F[Nomad Cloud Analytics]
