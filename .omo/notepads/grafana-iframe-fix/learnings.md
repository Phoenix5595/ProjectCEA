# Grafana Iframe Fix Learnings

## What Worked
- Individual panel embeds (`/d-solo/{uid}?panelId={id}`) work with authenticated session
- Grid layout with fixed heights using `calc(100vh - 2rem)` fits viewport
- Using `overflow-hidden` on containers prevents internal scrolling

## What Didn't Work
- Full dashboard embed (`/d/{uid}?kiosk=tv`) caused duplicate page rendering
- `allow_embedding` not needed when using same-session authentication via proxy
- iskradocker:3000 proxy target failed due to auth - localhost:3000 works

## Key Fixes Applied
1. GrafanaPanel.tsx: Simplified to use d-solo endpoint only
2. FlowerMonitoring.tsx: Grid layout with left sidebar (1/6) + main chart (5/6)
3. VegetationMonitoring.tsx: Same grid pattern
4. FlowerSoil.tsx: Fixed UID from 'flower-soil' to 'flower-sector-soil'
5. All pages: Added `overflow-hidden` and fixed heights to prevent internal scroll

## Layout Pattern
```
Page (overflow-hidden, height: calc(100vh - 2rem))
├── Header
└── Content (flex, h-full)
    ├── Sidebar (w-1/6, overflow-y-auto) - sensor list
    └── Main (w-5/6, overflow-hidden) - main chart
```

## Build Status
- Build passes: YES
- Commits: YES (cb2fbb7)
