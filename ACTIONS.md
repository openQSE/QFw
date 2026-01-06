# QFw Qiskit v2.3.0 Migration - Action Plan

## Architecture

```
QFWBackend (BackendV2)           [Existing - minimal updates]
    ├─ QPM Integration (DEFw)    [Preserve unchanged]
    ├─ QASM execution            [Keep existing]
    └─ Result conversion         [Keep existing]
         ↑
         │ wraps
         ├─────────────────┬─────────────────┐
         │                 │                 │
    QFWSamplerV2      QFWEstimatorV2    QFWBackend.run()
  (BaseSamplerV2)   (BaseEstimatorV2)  [backward compat]
```

## Action Items - ALL COMPLETED ✅

### ✅ Action 1: Update Requirements - COMPLETED
**File:** `setup/requirements.txt`
- ✅ Updated qiskit version from `==1.2.0` to `>=2.3.0rc1`

### ✅ Action 2: Update QFWBackend - Remove Deprecated APIs - COMPLETED
**File:** `backends/qfw_qiskit/qfw_simulator.py`

**Completed changes:**
- ✅ Removed all deprecated imports (assemble, disassemble, qobj, convert_to_target, BackendConfiguration, BackendProperties)
- ✅ Replaced Target creation with manual Target() construction
- ✅ Removed all assemble() usage from _run_sync_job() and _run_async_job()
- ✅ Simplified circuit execution to use circuits directly instead of QasmQobj
- ✅ Updated configuration() and properties() methods to return dicts instead of deprecated classes
- ✅ Extract circuit metadata directly from QuantumCircuit objects (name, num_clbits, cregs)
- ✅ All QPM integration, DEFw resource management, and event-based async execution preserved

### ✅ Action 3: Create QFWSamplerV2 - COMPLETED
**File:** `backends/qfw_qiskit/qfw_sampler.py` (NEW)

**Completed implementation:**
- ✅ Full BaseSamplerV2 implementation following BackendSamplerV2 reference pattern
- ✅ Wraps QFWBackend (BackendV2) for execution
- ✅ PUB (Primitive Unified Bloc) handling with parameter binding
- ✅ BitArray result format with proper memory conversion
- ✅ Helper functions: _prepare_memory(), _analyze_circuit(), _memory_array(), _samples_to_packed_array()
- ✅ Support for multiple shots configurations
- ✅ Classical register handling and metadata preservation

### ✅ Action 4: Create QFWEstimatorV2 - COMPLETED
**File:** `backends/qfw_qiskit/qfw_estimator.py` (NEW)

**Completed implementation:**
- ✅ Full BaseEstimatorV2 implementation following BackendEstimatorV2 reference pattern
- ✅ Wraps QFWBackend (BackendV2) for execution
- ✅ Pauli observable expectation value calculation
- ✅ Basis rotation circuits (X→H, Y→Sdg+H, Z→measure)
- ✅ Abelian grouping for commuting observables
- ✅ Precision to shots conversion: shots = ceil(1/precision^2)
- ✅ Variance and standard error computation
- ✅ Helper functions: _pauli_expval_with_variance(), _paulis2inds(), _parity(), _measurement_circuit()
- ✅ PassManager for gate optimization

### ✅ Action 5: Update Package Exports - COMPLETED
**File:** `backends/qfw_qiskit/__init__.py`

**Completed changes:**
- ✅ Added explicit imports for QFWBackend, QFWSamplerV2, QFWEstimatorV2
- ✅ Added QFWJob and defw_get_qpm_service exports
- ✅ Defined __all__ list for clean public API

## Implementation Summary - ALL COMPLETED ✅

1. ✅ Updated requirements.txt to Qiskit 2.3.0rc1
2. ✅ Updated QFWBackend (removed all deprecated APIs)
3. ✅ Created QFWSamplerV2 (complete implementation)
4. ✅ Created QFWEstimatorV2 (complete implementation)
5. ✅ Updated __init__.py exports

## Critical Constraints

### DEFw/QPM Integration (DO NOT MODIFY)
- `defw_get_resource_mgr()`
- `defw_reserve_service_by_name()`
- `BaseEventAPI()` event handling
- QPM methods: `create_circuit()`, `sync_run()`, `async_run()`
- Circuit submission via QASM strings
- Event-based result collection
- All RPC and event file descriptor code

### Backward Compatibility
- Existing `QFWBackend.run()` must continue working
- No breaking changes to public APIs
- Job interface unchanged

## Reference Files

**Qiskit 2.3.0rc1 implementations:**
- `qiskit-2.3.0rc1/qiskit/primitives/backend_sampler_v2.py`
- `qiskit-2.3.0rc1/qiskit/primitives/backend_estimator_v2.py`
- `qiskit-2.3.0rc1/qiskit/primitives/base/base_sampler.py`
- `qiskit-2.3.0rc1/qiskit/primitives/base/base_estimator.py`

**QFw files to modify:**
- `backends/qfw_qiskit/qfw_simulator.py`
- `backends/qfw_qiskit/qfw_sampler.py` (new)
- `backends/qfw_qiskit/qfw_estimator.py` (new)
- `backends/qfw_qiskit/__init__.py`
- `setup/requirements.txt`
