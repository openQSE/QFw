# QFw Requirements

Requirements documents are grouped here so implementation and release material
can link to the directory instead of depending on individual file names.

| Document | Description |
| --- | --- |
| [QFw requirements](qfw.md) | Core behavioral requirements for QFw operation modes, discovery, reservations, scheduling, API categories, runtime state, and provider credentials. Read this before changing externally visible QFw semantics. |
| [C service interface and RPC requirements](c-service-interface.md) | Requirements for the C-facing service interface, RPC behavior, and native integration boundaries. Read this when changing DEFw-facing C contracts or native service call paths. |
| [Slurm plugin requirements](slurm-plugin.md) | Requirements for Slurm quantum resource submission, reservation coupling, gateway behavior, and job lifecycle integration. Read this before changing qfw-slurm or cluster Slurm behavior. |
