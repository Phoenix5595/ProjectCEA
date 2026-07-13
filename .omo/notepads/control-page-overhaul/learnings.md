
## 2026-07-12 — Task 3: RelayChannelBox badge scaling

- Badge button is at line 164 of `RelayChannelBox.tsx`, className contains `text-[8px]` + `px-1 py-px`.
- Scaling to `text-[20px]` + `px-2 py-1` required NO parent container adjustment: the root box uses `overflow-visible` and the badge wrapper is `shrink-0 relative`, so the larger badge does not clip.
- The LED dot (`h-2 w-2` at line 142) is a separate element and must not be touched.
- `tsc --noEmit` and `npm run build` both pass cleanly after the change.

## 2026-07-12 — Task 2: getChannelDisplayName display_name fix

- `getChannelDisplayName()` is at lines 114-127 of `relayViewModel.ts`. It was changed to check `channel.display_name` FIRST (for all device types), then fall back to `light_name || device_name` for lights and `device_name` for non-lights.
- `ChannelInfo.display_name` field exists in `types/relay.ts:16` — backend already returns it in `/api/devices/channels`.
- The change is logic-only; no type signature change. `tsc --noEmit` passes with 0 errors.
- `assignedDeviceName` in `buildRelayChannelViewModels` (line ~199) deliberately still uses `channel.device_name` (canonical) for API calls — must NOT be changed to `display_name`.
- Pre-existing test failure: `relayMatrix.test.tsx > "shows relay numbers in left and right gutters"` fails on `main` without any changes. It expects standalone gutter numbers "1".."8" but `RelayChannelBox` renders "R1", "R16" etc. as combined spans. Unrelated to this task — do not try to fix it here.
