# ProjectCEA - Technical Context

> Essential technical info for AI assistants. Updated: 2026-01-13

**Canonical architecture:** For the full system schematic (data flow, services, Redis, DB, hardware, deployment), read **`ARCHITECTURE.md`** at project root. It is the single reference for current architecture and is updated when relevant changes are deployed.

## System Overview

Raspberry Pi 5 + 512GB NVMe running CEA automation for 2 grow rooms.
Ultimate goal: Max data for AI training (spike prediction, auto-tuning).

## Services

| Service | Port | Purpose |
|---------|------|---------|
| automation-service | 8001 | Control loop + frontend |
| cea-backend | 8000 | Dashboard API |
| can-processor | - | CAN bus to Redis/TimescaleDB |

## Hardware

- Pi 5 with 512GB NVMe SSD (not SD card)
- 3 ESP32 CAN nodes (Flower Back/Front, Veg Main)
- Sensors per cluster: 2x PT100, SCD30, BME280

## Data Flow



## Rooms

| Room | Schedule | Clusters |
|------|----------|----------|
| Flower Room | Night cycle | 1 (future: 2) |
| Veg Room | Night cycle | 1 |

## Database

- TimescaleDB (PostgreSQL)
- 1/sec sampling, 1yr full resolution
- Compression after 7 days
- Tables: measurement, effective_setpoints, automation_state

## Control

- VPD is master, humidity is slave
- Self-tuning PID with deadbands
- 4 modes: DAY, NIGHT, PRE_DAY, PRE_NIGHT

## Paths

- Dev: /home/antoine/ProjectCEA/
- Prod: /opt/projectcea/
- Git: https://github.com/Phoenix5595/ProjectCEA.git
