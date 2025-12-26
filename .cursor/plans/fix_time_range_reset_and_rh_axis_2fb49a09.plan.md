---
name: Fix time range reset and RH axis
overview: Stop time range from snapping back to 24h and hard-cap RH axis to 0–100% while still auto-ranging within that band.
todos:
  - id: guard-updates
    content: Add user-vs-programmatic guard in Graph onUpdate
    status: completed
  - id: stabilize-state
    content: Ensure timeRange from dropdown isn’t overwritten by Graph updates
    status: completed
    dependencies:
      - guard-updates
  - id: clamp-rh
    content: Clamp RH axis to 0-100 with internal autorange
    status: completed
---

# Fix Time Range Reset & RH Axis Clamp

## Goals

- Stop dropdown time range from snapping back to 24h (it currently snaps back after pan/zoom interactions).
- Keep RH axis within 0–100%, auto-ranging inside those bounds without exceeding them.

## Approach

1) **Guard programmatic vs user updates** in `Graph.tsx`

- Track Plotly updates triggered by layout changes vs user pan/zoom.
- Only call `onTimeRangeChange` for user-driven interactions (ignore programmatic relayouts and double-click resets).

2) **Stabilize time range state flow** between `Controls` → `RoomDashboard` → `Graph`

- Ensure dropdown changes propagate once and are not overwritten by graph `onUpdate`.
- Confirm `timeRange` used for data filtering and layout comes from React state only.

3) **Clamp RH axis to 0–100 with autorange inside**

- In `Graph.tsx`, set RH range calculation to clamp min/max to [0,100] and disable expansion beyond 100.
- Keep autorange within those caps (padding allowed inside 0–100 only).

## Files to edit

- `Infrastructure/frontend/src/components/Graph.tsx`
- (If needed for state flow) `Infrastructure/frontend/src/components/RoomDashboard.tsx`

## Notes / Assumptions

- RH data never exceeds 100 by design; clamp axis to 0–100 to avoid “impossible” scales.
- Time range currently snaps back after chart interaction; treat Plotly double-click/reset as user interaction, but avoid converting programmatic range sets back to 24h.