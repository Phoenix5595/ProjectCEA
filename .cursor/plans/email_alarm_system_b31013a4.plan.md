---
name: Email Alarm System
overview: Create an email alarm system that monitors temperature, humidity, and water level sensors, sends email alerts when thresholds are crossed, logs all alarms to a file, and uses a local SMTP server that can be easily moved to a home server.
todos:
  - id: create_alarm_config
    content: Create alarm_config.yaml with thresholds, email settings, and SMTP configuration
    status: pending
  - id: create_email_service
    content: Create email_service.py with SMTP client and email formatting functions
    status: pending
  - id: create_alarm_monitor
    content: Create alarm_monitor.py with threshold checking, cooldown logic, and alarm logging
    status: pending
    dependencies:
      - create_alarm_config
      - create_email_service
  - id: integrate_background_tasks
    content: Modify background_tasks.py to add alarm monitoring task
    status: pending
    dependencies:
      - create_alarm_monitor
  - id: update_main
    content: Update main.py to start alarm monitoring task in lifespan
    status: pending
    dependencies:
      - integrate_background_tasks
  - id: update_requirements
    content: Update REQUIREMENTS.md to document alarm system
    status: pending
---

# Email Alarm System Implementation

## Overview

Implement a backend email alarm system that monitors sensor parameters (temperature, humidity, water levels) and sends email alerts when thresholds are crossed. All alarms are logged to a file, and the system uses a local SMTP server that can be easily configured for a future home server.

## Implementation Details

### Files to Create

1. **`Infrastructure/backend/app/alarm_config.yaml`** - Configuration file for alarm thresholds and email settings

- Temperature thresholds (above threshold triggers alarm)
- Humidity thresholds (above threshold triggers alarm)
- Water level thresholds (above threshold triggers alarm - tank getting empty)
- Email recipient address
- SMTP server configuration (localhost for now, easily changeable to home server IP)

2. **`Infrastructure/backend/app/email_service.py`** - Email sending service

- SMTP client using Python's `smtplib`
- Support for local SMTP (localhost) and external SMTP servers
- Email formatting with alarm details (parameter, value, timestamp)
- Error handling and logging

3. **`Infrastructure/backend/app/alarm_monitor.py`** - Alarm monitoring service

- Monitor all temperature sensors (dry_bulb, wet_bulb, secondary_temp)
- Monitor all humidity sensors (secondary_rh)
- Monitor all water level sensors (water_level_*, water_level_main, water_level_1, etc.)
- Threshold checking logic
- Cooldown mechanism (1 minute per parameter)
- Alarm logging to file
- Integration with database manager to fetch latest sensor values

### Files to Modify

1. **`Infrastructure/backend/app/background_tasks.py`**

- Add alarm monitoring task that runs alongside sensor data broadcasting
- Check sensor values against thresholds periodically

2. **`Infrastructure/backend/app/main.py`**

- Start alarm monitoring task in lifespan context

3. **`Infrastructure/backend/REQUIREMENTS.md`**

- Document alarm system configuration and requirements
- Document alarm log file location

### Key Features

- **Threshold Monitoring**: All temperature, humidity, and water level sensors monitored
- **Cooldown Period**: 1-minute cooldown prevents duplicate alarms for the same parameter
- **Email Alerts**: Sends email with parameter name, triggered value, and timestamp
- **Alarm Logging**: All alarms logged to `Infrastructure/backend/alarms.log` (or configurable path)
- **Configurable SMTP**: Local SMTP (localhost) by default, easily configurable for home server via config file
- **Error Handling**: Graceful error handling to prevent alarm system from crashing the main application

### Default Thresholds (configurable in alarm_config.yaml)

- Temperature: 30°C (above triggers alarm)
- Humidity: 80% (above triggers alarm)
- Water Level: 200mm (above triggers alarm - tank empty)

### Alarm Log Format

Each alarm logged as: `[TIMESTAMP] ALARM: {parameter} = {value} {unit} (threshold: {threshold}) - Location: {location}/{cluster}`

### Email Format

Subject: `CEA Alarm: {parameter} threshold exceeded`
Body includes parameter name, current value, threshold, timestamp, and location/cluster information.