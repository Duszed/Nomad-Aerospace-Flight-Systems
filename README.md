# Nomad Aerospace — Flight Systems & Telemetry Repository

![License](https://img.shields.io/badge/License-Proprietary--Open%20Architecture-blue)
![Platform](https://img.shields.io/badge/Platform-ArduPilot%20%7C%20Cube%20Orange%2B-orange)
![Hardware](https://img.shields.io/badge/Hardware-30L%20Heavy--Lift%20UAV-green)

Welcome to the official core flight system documentation and custom telemetry firmware for **Nomad Aerospace** — Central Asia's premier Deep Tech agricultural UAV platform.

---

## 🚀 System Architecture Overview

```mermaid
graph TD;
    subgraph Nomad Aerospace Ecosystem
    A[Cube Orange+ Flight Controller] -->|MAVLink via 915MHz| B(Nomad Ground Control Station);
    C[Benewake TF03 LiDAR] -->|CAN Bus| A;
    D[77GHz 360 Radar] -->|CAN Bus| A;
    E[ESP32-C3 Field Node] -->|LoRa/WiFi| B;
    B -->|API/JSON| F[Cloud Analytics Dashboard];
    end
