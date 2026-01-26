# Draft: Fix Light Control Issue

## Requirements (confirmed)
- Fix data mismatch between 'schedules' table and 'mode_parameters' table causing light control failures
- Fix ramping functionality (automation reads 0 duration from wrong table)
- Fix lights staying at day intensity during night (mode resolution fails without active schedule)
- Ensure both Veg Room and Flower Room light controls work properly

## Technical Decisions
- Root cause identified: 'mode_parameters' table (UI) vs 'schedules' table (automation) data split
- Frontend UI uses mode_parameters (saved correctly) 
- Automation service reads from schedules table (contains incomplete data)
- Need to fix data flow between these tables

## Research Findings
- Critical data mismatch identified between schedules and mode_parameters tables
- Both rooms affected (Veg Room and Flower Room)
- Test infrastructure exists with comprehensive light control test coverage
- Existing tests for scheduler, ramping logic, and Redis resilience
- Good TDD patterns established (time injection, async mocking)
- Key files: ControlEngine._determine_light_ramp() fails to read ramp durations from schedules

## Open Questions
- What is the correct approach to fix the data synchronization?
- Should automation service read from mode_parameters instead?
- Should mode_parameters write to schedules?
- Is there a missing data synchronization mechanism?

## Scope Boundaries
- INCLUDE: Fix light control data synchronization between tables, restore ramping and day/night transitions
- EXCLUDE: Hardware changes, sensor modifications, major architecture changes