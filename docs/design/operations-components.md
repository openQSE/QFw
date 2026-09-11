# QFw Operational Components

QFw operations combine the framework repository, the DEFw runtime, QHW
libraries, provider adapters, Slurm integration, and the dashboard. The table
below gives a high-level map of the moving pieces and how they fit together.

| Component | Runs where | Role |
| --- | --- | --- |
| QFw | Application nodes and Quantum Platform Manager (QPM) service nodes | Provides the user-facing quantum framework, runtime setup commands, client APIs, example applications, and QPM implementations. The QPM services live in QFw and own backend-specific execution, admission, scheduling, telemetry, and service-control behavior. |
| DEFw | Application and service processes | Provides the distributed execution runtime beneath QFw. It starts Python/C service processes, handles RPC transport, manages worker events, and supplies the directory-service discovery path used by QFw clients and QPM services. |
| qhw-admission | QPM reservation path | Provides the admission-control library used by QPM services and qfw-slurm to evaluate reservations, capacity, policy limits, usage, and release state for quantum resources. |
| qhw-scheduler | QPM task path | Provides the QPU-local scheduler library used by QPM services to queue, select, and track reservation-scoped quantum tasks before provider execution. |
| qhw-data | Provider metadata and result paths | Defines provider-neutral schemas and builders for hardware data such as devices, couplings, calibrations, and execution results. QFw services use these schemas to expose backend data without binding clients to a vendor SDK shape. |
| qhw-iqm | IQM provider normalization paths | Converts IQM-native architecture, calibration, coupling, and result payloads into qhw-data records. It is an adapter layer only; QFw services still own provider calls, credentials, reservations, and execution lifecycle. |
| qhw-datastructures | qhw-admission and qhw-scheduler internals | Provides reusable C container primitives used by the QHW libraries. It is not normally visible to QFw users, but it is part of the built runtime dependency chain. |
| qfw-slurm | Slurm controller and job launch path | Maps Slurm quantum resource requests into QFw/QPM reservation calls, exports reservation metadata to jobs, and releases reservations as jobs complete. It is the bridge between site scheduling policy and QFw execution. |
| QFw-SLURM-Cluster | Docker and site-integration environment | Provides the integration harness for running QFw with Slurm, qfw-slurm, site service management, simulator and hardware-like nodes, and installed `/opt/openqse` and `/etc/openqse` layouts. |
| Dashboard | Operator browser and dashboard service | Provides GUI workflows for service control, resource reservations, application submission, topology/status inspection, result summaries, and log download. It uses the QFw-SLURM-Cluster environment and qfw-slurm/QFw service state rather than replacing those operational paths. |
