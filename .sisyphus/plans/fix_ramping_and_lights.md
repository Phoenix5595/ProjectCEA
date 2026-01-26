# Plan: Final Fix for Ramping, Lights, and Grafana (Revised, No Tests)

Context
- Address the logic gaps in the control system that cause lights to stay ON at night, setpoints to disappear from Grafana, and ramping to reset or skip during database hiccups or service restarts.
- This revision removes the dedicated test deliverable; testing scope will be incorporated into the verification plan later as manual QA and lightweight checks, without adding separate test scripts.

---

Work Objectives

### Core Objective
- Ensure the system always knows its mode, always logs its state, and correctly interpolates setpoints through system restarts and database outages using stateless, time-based logic, with robust logging and minimal disruption during outages.

### Concrete Deliverables (no tests)
- `Infrastructure/automation-service/app/control/control_engine.py`: Sticky Mode persistence and resilient logging.
- `Infrastructure/automation-service/app/control/device_processor.py`: Enforce light states (NIGHT/PRE_DAY = 0.0) with override protection.
- `Infrastructure/automation-service/app/control/setpoint_manager.py`: Stateless, time-based ramp progress calculation.

---

## TODOs

- [x] 1. Implement Stateless Time-Based Ramping
- [x] 2. Enforce Light States with Override Protection
   - Invariant requirement: There must always be an intensity level, a mode, and a period present in every path that governs lighting control.
   - What to do:
    - Modify `Infrastructure/automation-service/app/control/device_processor.py`:
      - In `process_devices`, detect a manual override using `self.device_controller.relay_manager.get_device_mode(location, cluster, device_name)`; treat it as an override when true.
      - If NO override AND `intensity_details` is `None`:
        - If mode is NIGHT or PRE_DAY: force `context["light_intensity"] = 0.0`
        - If mode is DAY or PRE_NIGHT: force `context["light_intensity"] = 1.0`
      - If an override is active, preserve override behavior (do not force off).
      - Ensure that in all cases, the resulting context includes:
        - `light_intensity` (0.0-1.0)
        - `mode` (string enum)
        - `period` (ramp/slot) to describe the active time window or phase
    - Maintain backward compatibility with existing logic elsewhere in the file.
    - Acceptance Criteria:
    - AC2.1 Lights turn OFF at night when there is no active schedule and no manual override, and intensity is defined.
    - AC2.2 Manual overrides are respected (override takes precedence over automatic ramp).
    - AC2.3 No unintended side effects for other non-lighting devices or paths.
    - Verification plan (hands-on):
    - Identify process_devices path in device_processor.py and insert logging around decision points (override detected, intensity_details value, mode, and period).
    - Create three scenarios in a test harness or live environment:
      - Scenario A: NIGHT mode, no schedule, no override → intensity forced to 0.0, mode= NIGHT, period= NIGHT_WINDOW.
      - Scenario B: NIGHT with manual override → intensity follows override, mode=NIGHT, period remains NIGHT_WINDOW.
      - Scenario C: DAY mode, no schedule, no override → intensity forced to 1.0, mode=DAY, period=DAY_WINDOW.
    - Verify that device actuation path receives expected intensity and that only lighting paths are affected.
    - Add logs for invariant violations (e.g., missing period or intensity)
    - Plan documentation updates:
      - Update .sisyphus/plans/fix_ramping_and_lights.md to include a dedicated Task 2 section with:
        - Exact target file
        - Patch surface (high-level changes, not code)
        - Invariant statement and rationale
        - Acceptance criteria (AC2.1–AC2.3)
        - Lightweight verification steps (scenarios A–C)
        - Expand “Agent Guidance & Compliance” section with the invariant guarantee
        - Add a short note in .sisyphus/notepads/fix_ramping_and_lights/learnings.md about the invariant policy and any decisions
    - Then proceed to implement patch as a single atomic change in a follow-up delegation.
- [ ] 3. Sticky Mode and Resilient Logging

---

## Success Criteria
- [x] Ramping is stateless on startup and starts within ramp windows when appropriate.
- [ ] Lights turn OFF at night when there is no active schedule and no override.
- [ ] Grafana shows continuous data, with logs of effective setpoints and ramp progress consistently available.

## Agent Guidance & Compliance (Derived from AGENTS.md)
- This plan adheres to the agent guidance in the AGENTS.md family of documents:
  - One atomic task per delegation; avoid multi-task batches; propose separate plan items for distinct work.
  - Use explore/librarian for discovery; manual Momus/Metis as available; otherwise manual rigorous review.
  - Drafts and notes stored under .sisyphus/drafts and notepads under .sisyphus/notepads.
  - The plan file (.sisyphus/plans/*.md) is the single source of truth; no implementation changes are made in this phase.
  - For any agent invocation, present a clear, single objective; if a conflict arises, refuse with a concrete alternative.
  - The seven-section delegation format is used when presenting plan progress and options.
  - External reference to agent docs is provided in this plan as a memory aid.
- References:
  - AGENTS.md at /home/antoine/ProjectCEA/AGENTS.md
  - Infrastructure AGENTS.md at /home/antoine/ProjectCEA/Infrastructure/AGENTS.md
  - Automation service AGENTS.md at /home/antoine/ProjectCEA/Infrastructure/automation-service/AGENTS.md
  - Code philosophy at /home/antoine/ProjectCEA/.opencode/philosophy/AGENTS.md
  - Sensor_Nodes AGENTS.md at /home/antoine/ProjectCEA/Sensor_Nodes/AGENTS.md
