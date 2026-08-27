# CEA Sensor Nodes

ESP32-based CAN bus sensor nodes. `ESP32/fullV6/fullV6.ino` is the current firmware authority; V4/V5 READMEs are archived under `archive/docs/2026-08-10/`.

## Current Firmware

- `ESP32/fullV6/fullV6.ino` — runtime sensor reconnection detection, native TWAI driver, GPIO 5 TX / 4 RX.
- Supports `NODE_ID` 1, 2, or 3 at compile time. Node 4/5 sensors are not emitted over this CAN protocol.
- Sensors per node: 2x MAX31865 + PT100 (dry/wet), BME280 (`0x76`/`0x77`), SCD30 (`0x61`), VL53L0X.

## CAN Protocol

- **Bitrate**: 250 kbps (`TWAI_TIMING_CONFIG_250KBITS()` in fullV6; must match the Pi `can0` setup).
- **ID scheme**: `0x1XX` = Node 1, `0x2XX` = Node 2, `0x3XX` = Node 3.
- **Frame layout**: PT100 `+0x01`, BME280 `+0x02`, SCD30 `+0x03`, VL53 `+0x04`, heartbeat `+0x05`.
- **Decoder coupling**: any change to frame layout or IDs must be mirrored in `Infrastructure/can-processor-service/app/decoder.py`. The decoder currently maps only Node 1, 2, and 3.

## Node Mapping

| Node | CAN base | Location | Sensor cluster |
|------|----------|----------|----------------|
| 1 | `0x100` | Flower Room | `back` (`_b`) |
| 2 | `0x200` | Flower Room | `front` (`_f`) |
| 3 | `0x300` | Veg Room | `main` (`_v`) |

`back`/`front` are sensor sub-clusters only, not the automation device cluster `main`.

## Upload / Node Safeguards

- Verify `#define NODE_ID` before every upload. A wrong node ID sends data to the wrong room.
- Use Arduino IDE or PlatformIO with ESP32 Dev Module.
- Changing the CAN bitrate requires updating every node **and** the mothernode CAN interface; do not change it on one node alone.
- Test firmware and `can_*` sketches stay off production nodes.

## Where to Look

| Topic | Document |
|-------|----------|
| FullV6 details | `ESP32/fullV6/README.md` |
| CAN decoder | `Infrastructure/can-processor-service/app/decoder.py` |
| Archived V4/V5 | `archive/docs/2026-08-10/` |
| Cluster topology | `ProjectCEA/AGENTS.md` |

---

*Last updated: 2026-08-10*
