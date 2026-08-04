Welcome to the official core flight system documentation and custom telemetry firmware for **Nomad Aerospace** — Central Asia's premier Deep Tech agricultural UAV platform.

---

## 🚀 System Overview
graph TD;
    subgraph Nomad Aerospace Ecosystem
    A[Cube Orange+ Flight Controller] -->|MAVLink via 915MHz| B(Nomad Ground Control Station);
    C[Benewake TF03 LiDAR] -->|CAN Bus| A;
    D[77GHz 360 Radar] -->|CAN Bus| A;
    E[ESP32 Field Node] -->|LoRa/WiFi| B;
    B -->|API/JSON| F[Cloud Analytics Dashboard];
    end
    
Nomad Aerospace engineers 30L / 60kg heavy-lift autonomous UAVs designed specifically for high-efficiency precision agriculture, crop dusting, and localized environmental telemetry across Central Asia.

### Key Hardware & Avionics Specifications
* **Flight Controller:** Hex Cube Orange+ (Triple-Redundant IMU, Vibration Isolated)
* **Firmware Base:** ArduPilot Copter v4.5+ (Custom Parameter Architecture)
* **Propulsion:** 4x Hobbywing X11 G2 FOC Integrated Motors (18S DC, 34kg Peak Thrust/Axis)
* **Primary Navigation:** Dual-Antenna Here4 RTK GNSS (Centimeter-Level Accuracy, GPS-Compass Interference Immunity)
* **Sensor Fusion Safety Shield:**
  * **Altimetry & Forward Obstacle:** Benewake TF03 Long-Range Industrial LiDAR (UART/CAN)
  * **Omnidirectional Shield:** 360° Millimeter-Wave Radar Ring (77GHz CAN Bus)
* **Spray Delivery System:** Dual Brushless Diaphragm Pumps + High-Speed Centrifugal Atomizing Nozzles (Variable Rate Application)

---

## 📂 Repository Structure
├── config/
│ └── ardupilot_30L_k30_frame.param # Production ArduPilot parameter stack
├── telemetry/
│ └── esp32_field_sensor_node.ino # ESP32 low-power agricultural IoT telemetry firmware
├── docs/
│ └── SENSOR_FUSION_ARCHITECTURE.md # Technical breakdown of Radar + LiDAR integration
└── README.md # Repository documentation
code
Code
---

## 🛠 Features & Implementations

### 1. Failsafe & Sensor Fusion Protocols
* **GPS-Loss Failsafe:** Automatic switch to `AltHold` with active LiDAR terrain-following and Radar boundary hold.
* **Smart Battery Failsafe:** Dual-stage voltage monitoring on 18S LiPo/Solid-State architectures.
* **Variable Rate Application (VRA):** PWM/CAN pump speed modulation tied directly to ground speed and NDVI multispectral data maps.

### 2. Edge Telemetry Node (`/telemetry`)
Custom firmware developed for low-power ESP32 edge modules, providing real-time microclimate monitoring (temperature, humidity, ambient pressure) transmitted via encrypted duty-cycled telemetry packets to preserve battery life in off-grid field deployments.

---

## 📄 License & Intellectual Property

Copyright © 2026 Nomad Aerospace. All rights reserved. Hardware parameter profiles and custom telemetry firmware licensed for Nomad Aerospace deployment and partners.
