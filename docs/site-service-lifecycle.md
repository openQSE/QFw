# QFw Service Ownership and Run Directories

QFw separates application runtimes from site services. Each `qfw-setup`
invocation owns one application run directory. A site administrator starts the
directory service and each QPM independently so that one directory can serve
multiple QPMs and many application runs.

## Ownership

| Resource | Owner | Lifecycle interface |
| --- | --- | --- |
| Application runtime | Application launcher or Slurm job | `qfw-setup`, `qfw-status`, `qfw-srun`, `qfw-teardown` |
| Application directory service | Application runtime | `qfw-setup` through the `qfw-dir-svc` lifecycle engine |
| Application QPM and optional DVM | Application runtime | `qfw-setup` through one `qfw-qpm-svc` manager per QPM |
| Site directory service | Site administrator or service manager | `qfw-dir-svc` |
| Site QPM and optional DVM | Site administrator or service manager | `qfw-qpm-svc` |
| Provider credentials | Site administrator and QPM process | Protected device-access configuration |
| Application reservation | Trusted application launcher | Reserve before execution and release after completion |

Shutdown follows ownership. `qfw-teardown` stops only managers recorded as
application-owned. It never stops a site directory service or site QPM. A site
manager stops only the component recorded in its own run directory.

The role managers use a private runtime module when a DEFw process must be
started on another node. Applications and administrators use only the public
lifecycle commands above.

## Application Runtime Directory

`qfw-setup` creates one directory for one logical application run. Several
`qfw-srun` steps may share it before teardown.

```text
<application-run-dir>/
├── qfw-runtime-env.sh
├── logs/
├── state/
│   └── runtime-state.json
└── service-plane/                  when the runtime owns services
    ├── directory/
    └── qpm/
        └── <service-id>/
```

`runtime-state.json` records the run identifier, selected configuration,
allocation, generated endpoints, setup state, and owned manager run
directories. `qfw-status` reads this file and composes current manager health.
`qfw-teardown` stops managers in reverse order, clears the current-run marker,
and removes the application directory unless `--keep-run-dir` is specified.

## Site Directory-Service Run Directory

One `qfw-dir-svc` invocation manages one directory-service instance:

```text
<directory-run-dir>/
├── qfw-service-env.sh
├── state/
│   └── service-plane.json
└── services/
    └── <directory-name>/
        ├── pid
        ├── ready.json
        └── logs/
```

The administrator supplies `directory-service.connection-file` in
`site.yaml`. The manager writes a client-readable JSON record containing the
resolved name and endpoint. That connection file may live outside the manager
run directory, but service and application nodes must see it at the configured
pathname.

The directory manager does not own QPMs. It can be placed on a central node
while individual QPM managers run elsewhere.

## Site QPM Run Directory

One `qfw-qpm-svc` invocation manages one QPM and, when required, its PRTE DVM:

```text
<qpm-run-dir>/
├── qfw-service-env.sh
├── state/
│   └── service-plane.json
├── services/
│   └── <service-id>/
│       ├── pid
│       ├── ready.json
│       ├── service-ready.json
│       └── logs/
└── prte_dvm/                       simulator backends only
    └── dvm-uri
```

The QPM manager reads the directory connection record automatically from
`site.yaml`. Users do not source `qfw-service-env.sh` to connect the QPM. The
manager starts PRTE before the QPM and stops the QPM before PRTE.

| Backend | PRTE DVM |
| --- | --- |
| Real IQM | Not required |
| fakeIQM | Not required |
| NWQ-Sim | Required when configured for MPI/DVM launch |
| TNQVM | Required when configured for MPI/DVM launch |

Application jobs connect through the directory service and QPM APIs. They do
not consume a site QPM's DVM URI.

## Shared Filesystem Requirement

An application run directory must be visible at the same pathname on every
participating node when application-owned services or a DVM span nodes. Set
`QFW_RUN_BASE_DIR` to shared storage before `qfw-setup` for these executions.

A site directory run directory needs to be visible only to its manager and
directory node, although its configured connection file must be visible to
clients. A QPM run directory must be visible at the same pathname on every
simulator node when that QPM owns a multinode DVM. Real IQM and fakeIQM QPMs
do not require a shared DVM URI.

## Lifecycle Commands

Start and inspect a site directory service:

```bash
qfw-dir-svc start \
  --run-dir "$DIR_RUN_DIR" \
  --site-config "$QFW_SITE_CONFIG" \
  --scope site \
  --node "$DIR_NODE"
qfw-dir-svc status --run-dir "$DIR_RUN_DIR"
```

Start one site QPM after the directory connection record is ready:

```bash
qfw-qpm-svc start \
  --run-dir "$QPM_RUN_DIR" \
  --site-config "$QFW_SITE_CONFIG" \
  --scope site \
  --service-id "$SERVICE_ID" \
  --node "$QPM_NODE"
qfw-qpm-svc status --run-dir "$QPM_RUN_DIR"
```

Stop site components in dependency order:

```bash
qfw-qpm-svc stop --run-dir "$QPM_RUN_DIR"
qfw-dir-svc stop --run-dir "$DIR_RUN_DIR"
```

Use the foreground `run` action instead of `start` under systemd or another
service supervisor. SIGINT and SIGTERM trigger manager-owned cleanup.

## Retention and Credentials

Run directories contain generated state, process identifiers, readiness
records, and logs. They must not contain provider API keys. The QPM receives
only the path to the protected device-access configuration selected by
`site.yaml`. Applications receive connection information but not credential
files.

Normal application teardown removes its run directory. Site manager state and
logs remain until the site administrator archives or removes them.
