## Decision: Use Custom Playwright Script
- **Rationale**: The Playwright MCP server was defaulting to 'chrome' which is unavailable on Arm64 Linux. A custom  script using the installed  package allowed specifying  and provided better control over WebSocket wait times.
- **Outcome**: Successful capture of all 4 baseline screenshots.
## Decision: Use Custom Playwright Script
- **Rationale**: The Playwright MCP server was defaulting to 'chrome' which is unavailable on Arm64 Linux. A custom `.cjs` script using the installed `playwright` package allowed specifying `chromium` and provided better control over WebSocket wait times.
- **Outcome**: Successful capture of all 4 baseline screenshots.
## Reset to Baseline
Reset ui/frontend-modernization to main to ensure a clean slate for the new modernization effort.
