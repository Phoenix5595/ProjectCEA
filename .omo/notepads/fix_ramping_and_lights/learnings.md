# Learnings from Fix Ramping and Lights Planning

- Rationale for plan revision: remove test scripts and Python-based tests to focus on runtime ramp correctness and stability.
- Observed issues summary:
- - Ramping startup behavior: stateless ramping is viable and preferred for startup; mode-change resets can disrupt ramps if not guarded.
- - Light-off at night: ensure NIGHT mode forces 0% when no active schedule; guard for edge-case where intensity_details may linger.
- - Data integrity: ensure setpoints carry consistent values through DB outages and restore ramps from Redis correctly.
- Proposed next steps: patch plan with single-atomic tasks; implement and test targeted changes; document guardrails and acceptance criteria clearly.

This document is intended as external memory and a record of decisions for future audits.
