ProjectCEA - High-Level Architecture Overview

Scope
- Raspberry Pi 5 based CEA automation system controlling two grow rooms.
- 6 Python FastAPI microservices under Infrastructure/ to expose control loops, sensor data, and APIs.
- ESP32 sensor nodes connected via CAN bus with a CAN-processor service ingesting CAN frames.
- Data storage via Redis (state + streams) and TimescaleDB (history/metrics). Visualization via Grafana and frontend React UI.

Key Dataflow (high level)
- ESP32 sensors produce data and status over CAN bus.
- can-processor-service ingests CAN frames, writes derived metrics to Redis and pushes streams (sensor:raw) as needed, and stores time-series data in TimescaleDB.
- Automation-service consumes recent state from Redis, runs deterministic control loops, and exposes REST endpoints for the frontend and external integrations.
- backend (Sensor API) exposes sensor data from Redis/TimescaleDB to clients.
- Grafana dashboards visualize history stored in TimescaleDB; frontend UI queries the same APIs for near real-time control surfaces.

Core Components & Responsibilities
- Infrastructure/automation-service: control loop, decision logic, orchestration between sensors and actuators, REST API surface.
- Infrastructure/backend: sensor data APIs for external access; data normalization and rate limiting.
- Infrastructure/can-processor-service: low-latency CAN bus ingestion, data decoding, error handling, and persistence coordination.
- ESP32 firmware: sensing and actuation logic; publishes CAN frames with sensor readings.
- Redis: fast in-memory state store for live readings and lightweight event streams for recent data.
- Redis Streams: ephemeral ingestion of raw sensor data and control events.
- TimescaleDB: durable time-series storage with hypertables; supports high-throughput writes and efficient historical queries.
- Frontend (React) + Grafana: UI dashboards for operators and dashboards for monitoring.

Data Flow Characteristics
- Asynchronous, event-driven data paths (CAN > can-processor > Redis/TimescaleDB).
- Deterministic control loop in Automation service with 1-second sampling cadence.
- DB writes batched to meet 100ms batch constraints (where applicable) and ensure deterministic write patterns.

Deployment & Operational Model
- Deployment: deploy.sh to push new releases; rollback.sh for quick rollback.
- Production path is read-only from development environments; changes go through CI and deployment tooling.
- Phases: Phase 1 focuses on Reliability (robustness, testing, observability) before feature expansion.
- Observability: structured logging, health endpoints, basic metrics; Grafana dashboards for runtime insights.

Constraints & Non-Negotiables
- 1 request per second (sampling) constraint for control loop stability.
- 100 ms DB batching constraint for TimeScale writes.
- No bare excepts; full TDd for new code; tests required for any code changes.
- Type-safety commitments: avoid as any / @ts-ignore in TypeScript layers; rigorous typing across services.
- Rollback readiness: quick rollback to a known-good state.

Risks & Mitigations
- CAN bus/ESP32 failures: implement watchdog and retry/backoff policies; isolate CAN errors to prevent control loop disruption.
- Data loss during bursts: Redis streams buffering with durable TimescaleDB writes; backpressure handling.
- Deployment failures: rollback.sh; blue/green style or canary where feasible; clear rollback criteria.

Future Enhancements (potential)
- Additional sensors and actuators integration; more granular data retention policies; more advanced control algorithms with online adaptation.

Notes
- This document is a living artifact and should be updated as the architecture evolves.
