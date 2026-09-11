# QFw Design Documentation

Design notes are grouped here so top-level documentation remains focused on
users and releases.

| Document | Description |
| --- | --- |
| [QFw design](qfw.md) | Main architecture and implementation design for QFw-managed services, QPM discovery, admission and scheduler integration, API categories, telemetry, and lifecycle behavior. Read this first when changing core runtime or service behavior. |
| [QFw service lifecycle](qfw-service-lifecycle.md) | Defines how QFw interprets QPM service records, service-type filtering, stale bindings, reconnect behavior, and lifecycle event handling. Use this when debugging discovery, reconnect, or QPM binding behavior. |
| [Site service lifecycle](site-service-lifecycle.md) | Explains ownership and run-directory boundaries for long-running site services. Use this when changing service startup, teardown, logging, or operator recovery flows. |
| [QPU front-end contract](qpu-frontend-contract.md) | Describes provider-facing metadata and device access expectations for QPU front ends. Use this when changing provider descriptors, credential forwarding, or backend metadata normalization. |
| [Slurm plugin design](slurm-plugin.md) | Design for the Slurm quantum plugin and how Slurm requests map into QFw reservations and service selection. Read this when changing qfw-slurm integration or scheduler-facing resource behavior. |
| [Slurm plugin driver refactor](slurm-plugin-driver-refactor.md) | Detailed design for splitting and hardening the Slurm plugin driver path. Use this for changes around qfw-slurm driver execution, gateway communication, or native/client boundaries. |
| [Libfabric transport](libfabric-transport.md) | Design notes for DEFw libfabric and transport integration used by QFw. Read this when transport behavior, OFI support, or large-message paths are involved. |
| [Benchmarking and profiling](benchmarking.md) | Benchmarking and telemetry design for measuring QFw overhead and exporting profiling data. Use this when adding metrics, traces, or performance-sensitive instrumentation. |
| [Implementation plan](implementation-plan.md) | Phase-by-phase implementation plan for the v0.1 managed execution work. Useful for reconstructing why commits were split or for checking remaining implementation gaps. |
| [Test plan](test-plan.md) | System-level validation plan for QFw-managed and long-running service modes. Use this to decide which behavior needs simulator, Slurm, or hardware-backed coverage. |
