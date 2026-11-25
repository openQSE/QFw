# Run QFw Against the IQM Garnet Mock

This recipe runs the QFw examples against IQM Resonance's noiseless 20-qubit
Garnet mock simulator. It needs an IQM Resonance API key but will not run the
examples on any actual quantum hardware.

To start, activate QFw inside a Slurm allocation first, as described in
[the examples README](../../examples/README.md).

## 1. Copy the templates

Copy the two templates from `examples/` to a directory you own, dropping the
`.example` suffix from the credentials copy.

```bash
mkdir -p "$HOME/garnet-mock" && cd "$HOME/garnet-mock"
cp "${QFW_SHARE_DIR}/examples/qfw_garnet_mock_device_access.yaml" .
cp "${QFW_SHARE_DIR}/examples/qfw_garnet_mock_creds.json.example" \
   qfw_garnet_mock_creds.json
chmod 600 qfw_garnet_mock_creds.json
```

The device-access file finds the credentials through a relative path, so keep
the two together.

## 2. Add your username and API key

Edit `qfw_garnet_mock_creds.json`. Replace `REPLACE_WITH_YOUR_USERNAME` with
the account name that runs the example, and
`REPLACE_WITH_YOUR_IQM_RESONANCE_API_KEY` with your IQM Resonance API key.
The file holds a live API key, so never commit it.

## 3. Run

```bash
export QFW_EXAMPLE_DEVICE_ACCESS_CONFIG="$HOME/garnet-mock/qfw_garnet_mock_device_access.yaml"
cd "${QFW_SHARE_DIR}/examples"
./qfw_ghz.sh --service-mode local qiskit 4 iqm-garnet-mock 1
./qfw_run_all.sh --service-mode local --backend iqm-garnet-mock
```
