# Pulsate Scientific Workspace

This directory contains the Pulsate Labs scientific workspace: a custom React application shell around a controlled Mol* molecular viewer. A displayed, discovered preset can now run through the existing verified local quantum-preflight workflow. Natural-language compilation, Qwen repair, and IBM Quantum execution are not connected.

## Run locally

Requirements: npm with a current Node.js LTS release and the repository's Python environment.

1. Install frontend dependencies:

   ```powershell
   cd frontend
   npm install
   ```

2. From the repository root, enable local execution and start FastAPI on the port expected by the development proxy:

   ```powershell
   $env:PULSATE_EXECUTION_ENABLED = "true"
   $env:PULSATE_RUN_ROOT = "$PWD\.pulsate-runs"
   python -m uvicorn cgr.pulsate_api.app:app --app-dir src --host 127.0.0.1 --port 8000 --reload
   ```

3. In a second terminal, start Vite:

   ```powershell
   cd frontend
   npm run dev
   ```

4. Open [http://127.0.0.1:5173](http://127.0.0.1:5173).

Vite proxies every `/api` request to `http://127.0.0.1:8000`. Choose a verified preset, wait until it is the displayed scene, then use **Run experiment**. The button is disabled for stale scene identity or unavailable capability. The browser persists only the active run identifier for refresh recovery; it never stores scientific results. Missing pinned quantum dependencies fail truthfully and never trigger fixture fallback data.

## Validate

From `frontend/`:

```powershell
npm run build
npm run lint
npm test -- --run
```

From the repository root, validate the existing API independently:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pytest tests/test_pulsate_api.py tests/test_pulsate_runs.py
```

## Interface states

The workspace uses progressive disclosure. After startup it fetches the verified preset catalogue but does not automatically choose or render a molecule. The initial workspace contains the scientific-objective composer, a dynamic **Use a preset** menu, a blank molecular canvas, and a short introduction. Natural-language experiment planning is not connected, so **Continue** remains disabled and never fabricates an experiment.

After the user explicitly selects a verified preset, the Mol* structure workspace and scientific inspector replace the empty state. Structure, workflow, results, and evidence navigation appear only while a valid scene exists. If a later preset fails to load, the retained scene keeps its original displayed-preset identity and the warning names that prior preset.

## Architecture

The data path deliberately has four boundaries:

```text
raw API contracts
  → Pulsate scene normalization
  → Mol* structure / representation adapter
  → isolated Mol* plugin state
```

- `src/api/` owns raw backend response types, runtime response checks, cancellation, and transport errors.
- `src/scene/types.ts` defines the renderer-independent scene: arbitrary atom and bond arrays, selections, regions, measurements, provenance references, warnings, and scientific metadata.
- `src/scene/normalize.ts` is the only place that interprets the current endpoint's diatomic-oriented bond distance fields. A measurement keeps its declared value, backend-derived value, and independently calculated viewer geometry separate, with differences where available.
- Provenance identity is lossless: `structureId`, backend-only `structureHash`, `experimentFingerprint`, and manifest `expectedExperimentSha256` are independent fields. An experiment fingerprint is never used as a structure-hash fallback.
- `src/scene/molstar-adapter.ts` serializes current coordinate scenes to in-memory MOL2. Original coordinates and their declared unit remain unchanged in the normalized scene; only the Mol* boundary converts coordinates to angstrom. Unknown connection order is written as MOL2 `un`, rather than inventing a chemical bond order. When the backend later supplies standard mmCIF, SDF, MOL, XYZ, trajectory, or volume artifacts, format routing can be added at this adapter boundary.
- `src/components/MolstarViewer.tsx` owns one `PluginContext`, cleans it up on unmount, maps Mol* atom picks back to stable Pulsate atom identifiers, uses generic bounds-derived fitting, adds Mol* distance and atom-label representations, and creates translucent Mol* representations for declared regions. A latest-scene queue serializes plugin mutations so rapid preset changes converge on the newest accepted scene.
- The rest of the product UI remains normal React and never receives Mol* objects.

Supported coordinate-unit aliases cover angstrom/angstroms/ångström/Å, bohr/bohrs/a0, and nanometre/nanometer/nm. Unknown units fail visibly instead of being treated as angstrom. Bond inference converts its angstrom covalent-radius thresholds into the scene's declared coordinate unit.

When a coordinate scene omits its `bonds` field entirely, `src/scene/geometry.ts` performs isolated, deterministic covalent-radius inference for visualization only. It does not use molecule names, skips pairs containing unknown elements, does not infer bond order or chemistry semantics, and is skipped above 500 atoms to bound quadratic work. That safety skip produces a visible scene warning. Explicit backend bond arrays—including explicit empty arrays—always take precedence.

`selectedPresetId` and `displayedPresetId` are separate lifecycle state. If a newly selected preset fails API validation, normalization, or loading, the last valid scene remains displayed under its original identity and the UI explicitly says which prior preset supplied it.

H2 and LiH are current backend fixtures, not frontend limitations. The normalized model, camera bounds, adapter, rendering loops, selections, regions, and tests accept more than two atoms. The Mol* boundary is intentionally suitable for future larger molecules, macromolecules, ligands, pockets, trajectories, volumetric data, orbitals, and interaction overlays.

## Current extension points and truthful limitations

- Quantum and active regions are represented through Mol* structure components and translucent representations; measurements use Mol* measurement representations.
- Atom labels are Mol* label representations derived from normalized element and atom identifiers. They are enabled automatically for structures containing at most 64 atoms and hidden above that threshold to protect clarity and rendering cost.
- Provenance is retained in the normalized scene and is ready for a dedicated evidence drawer, but current API scene payloads do not provide a public artifact graph to visualize.
- Fragment, residue, chain, pocket, interaction, computational-state, orbital, and density overlays have type and adapter boundaries but cannot be populated truthfully from the current coordinate-only scene response. They will be added as backend artifacts and selections become available—no placeholder scientific data is fabricated.
- Exact and VQE values, verification, authorization, and receipt state are displayed only from backend run evidence. IBM remains **Not configured**.
- The coordinator is a single-process development facility, not a production-distributed queue. Backend restarts mark active work `interrupted` and do not silently restart it.

See [`../docs/architecture/pulsate-run-api.md`](../docs/architecture/pulsate-run-api.md) for endpoints, statuses, durable persistence, recovery, executor selection, environment variables, and limitations.
