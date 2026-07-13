---
slug: device-registry-fix
status: awaiting-approval
intent: clear
pending-action: $start-work
approach: 3-wave plan — Wave 1: migration 010 (canonicalize device_name) + fix get_all_devices_flat error handling → deploy. Wave 3: remove dead YAML endpoints + schema + YAML fallback → deploy.
---

# Draft: device-registry-fix

## Components (topology ledger)
| id | outcome | status | evidence |
|----|---------|--------|----------|
| Migration 010 | Canonicalize non-light device_name, move human-readable to display_name | active | DB has 7 non-light rows with human-readable names in device_name, NULL display_name |
| Device model fix | Per-row error handling in get_all_devices_flat (skip bad rows) | active | devices.py:300 catches exception for entire list, returns [] |
| Dead code removal | Remove POST/DELETE channels endpoints, ChannelDeviceUpdate schema, YAML fallback, YAML devices block | active | 2 endpoints write to YAML, zero frontend callers; config.py:296-297 YAML fallback |

## Open assumptions (announced defaults)
None — user answered all forks explicitly.

## Findings (cited)
- `config.py:290-308` — get_devices() has YAML fallback when `_device_repo is None`
- `devices.py:559-658` — POST /api/devices/channels/{channel} writes to YAML, zero frontend callers
- `devices.py:662-720` — DELETE /api/devices/channels/{channel} writes to YAML, zero frontend callers
- `api.ts:303` — updateChannelDevice() method, zero callers in components
- `api.ts:329` — clearChannelDevice() method, zero callers in components
- `models/device_registry.py:37-40` — Device.device_name regex `^[a-z][a-z0-9]*_[fvlo]_\d+$`
- `repositories/devices.py:287-302` — get_all_devices_flat() catches exception for entire list, returns []
- `009_seed_device_registry_from_yaml.py:99` — put YAML key into device_name for non-lights (the bug)
- DB has 13 rows: 6 lights (correct), 7 non-lights (device_name has human-readable names, display_name NULL)

## Decisions (with rationale)
- Canonical naming: `<type>_<room>_<index>` (user chose this)
- Migration approach: alembic migration 010 (user chose this)
- YAML removal: full purge of devices: block + endpoints + fallback (user chose this)
- Wave structure: 3 waves + 2 deploys (user chose this)

## Scope IN
- Migration 010 canonicalize device_name + populate display_name
- Fix get_all_devices_flat per-row error handling
- Remove POST/DELETE /api/devices/channels/{channel} endpoints
- Remove ChannelDeviceUpdate schema + dead frontend methods
- Remove YAML fallback in config.get_devices()
- Remove devices: block from automation_config.yaml

## Scope OUT
- No ErrorBoundaries or frontend hardening
- No auto-migration in deploy.sh
- No LightDevice changes
- No automation_config.yaml removal (keep hardware/control/sensors)
- No write_full_config() removal (still used by system_config.py)

## Approval gate
status: awaiting-approval
pending-action: $start-work
approach: 3-wave plan with 2 deploys — Wave 1 (migration + model fix → deploy), Wave 3 (dead code removal → deploy)
