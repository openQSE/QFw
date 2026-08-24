# Recover QFw Services After an Interruption

Use the role manager state before deleting files or starting replacement
services. Never kill processes based only on a name or an unverified PID.

## 1. Inspect ownership and readiness

`qfw-dir-svc(1)` and `qfw-qpm-svc(1)` define the status, stop, and restart
behavior used throughout this recipe. Run `man 1 qfw-dir-svc` and
`man 1 qfw-qpm-svc` for their options, state files, and exit status.

```bash
qfw-dir-svc status --run-dir "${DIR_RUN_DIR}" || true
qfw-qpm-svc status --run-dir "${QPM_RUN_DIR}" || true

cat "${DIR_RUN_DIR}/state/service-plane.json"
cat "${QPM_RUN_DIR}/state/service-plane.json"
```

The role commands emit their generated state as JSON.

## 2. Stop recorded components

```bash
qfw-qpm-svc stop --run-dir "${QPM_RUN_DIR}" || true
qfw-dir-svc stop --run-dir "${DIR_RUN_DIR}" || true
```

These commands act only on components recorded in their respective manager
state. Stop the QPM before the directory.

## 3. Resolve common failures

### Stale PID or readiness file

Read the recorded node and PID from `state/service-plane.json`. On that exact
node, verify both the PID and full command line:

```bash
srun --nodes=1 --ntasks=1 --nodelist "${RECORDED_NODE}" \
  ps -o pid=,lstart=,args= -p "${RECORDED_PID}"
```

If the process is absent, retain the state and logs for diagnosis, then move
the complete stopped run directory to an archive location. Do not delete only
the PID file and reuse partially generated state.

### Missing DVM URI

Inspect the QPM manager log and PRTE output under `${QPM_RUN_DIR}`. Confirm that
the run directory is shared at the same pathname on every simulator node and
that each node can execute `prte` and `pterm`. Stop the QPM manager before
starting a replacement DVM.

### Failed QPM registration

Check the directory connection record configured by
`directory-service.connection-file`, then verify the directory status, name
resolution, service port, and firewall policy. Restart the QPM only after the
directory reports ready. A QPM restart does not require restarting a healthy
directory service.

### Leaked reservation

Do not remove manager state to hide a reservation. Inspect the application
driver JSONL and QPM logs for the reservation ID and release result. Use the
QPM admission/control API or the site operator procedure to release or cancel
the exact reservation, then verify that it reaches a terminal state.

## 4. Restart cleanly

Archive the old run directory and use a new empty directory. Keep the same
site configuration and connection-file path when clients should reconnect to
the replacement service:

```bash
mv "${QPM_RUN_DIR}" "${QPM_RUN_DIR}.failed.$(date +%Y%m%d-%H%M%S)"
mkdir -p "${QPM_RUN_DIR}"
qfw-qpm-svc start \
  --run-dir "${QPM_RUN_DIR}" \
  --site-config "${QFW_SITE_CONFIG}" \
  --runtime-config "${SERVICE_RUNTIME_CONFIG}" \
  --scope site \
  --service-id nwqsim \
  --node "${QPM_NODE}"
```

If the directory itself failed, stop all dependent QPMs first, archive its run
directory, restart the directory, and then restart each QPM.
