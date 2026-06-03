- Initial removal of the footer button section caused a TS error due to an extra closing div. Fixed by carefully matching open/close tags.
## 2026-03-03 Task 4: Lab Sensor Diagnosis
Findings:
- onewire-worker service is active and healthy.
- Redis keys sensor:lab_temp and sensor:water_temperature are nil.
- /sys/bus/w1/devices/ is empty (no 28-* devices found).
- Kernel modules are loaded.
Conclusion: Hardware connection or GPIO 24 configuration issue. Sensors are not detected by the OS.
