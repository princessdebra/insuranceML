# UNDERWRITING_AI — Motor Insurance Fraud Detection System
---

## Project Overview

UNDERWRITING_AI is a physics-based motor insurance fraud detection system built for Old Mutual Kenya. Its core purpose is to automatically cross-check the plausibility of motor vehicle accident claims by applying real-world crash physics to the information submitted by claimants, assessors, and repair shops.

The system detects fraud by reconstructing what *should have happened* based on the laws of physics, then comparing that reconstruction against what claimants *say* happened. Discrepancies — in speed, crush depth, damage patterns, and repair costs — are scored and flagged for investigation.

The system is multi-party: a single claim flows through three independent submissions (member, assessor, repair shop), and each submission is cross-verified against the others. Fraud analysis is hidden from all submitting parties and surfaced only to Old Mutual administrators and SIU investigators.

---

## Architecture Overview

```
Member / Assessor / Repair Shop
        │
        ▼
   routes.py  (FastAPI submission endpoints)
        │
        ▼
 pipeline_bridge.py  (normalises payload, determines pathway)
        │
        ├── Pathway 1 (telemetry / photo)
        │       ├── pixel_to_3d_transformer.py  (photo → 3D damage)
        │       └── accelerometer + GPS data
        │
        └── Pathway 2 (narrative only)
                └── NLP inference from claim text
        │
        ▼
  physics_engine.py  (McHenry crush energy + Newton-Euler reconstruction)
        │
        ├── vehicle_registry.py  (vehicle mass, stiffness, dimensions)
        ├── registry_adapter.py  (DB-backed vehicle + friction lookup)
        └── configurable_multi_vehicle_engine.py  (simulation timeline)
        │
        ▼
 render_simulation_video.py  (MP4 visualisation for investigators)
        │
        ▼
  reconstruction_routes.py  (admin/SIU endpoints — full fraud report)
        │
        ▼
    routes.py  (admin analysis endpoints: full-report, summary, all-claims)
```

---

## File-by-File Reference

### `vehicle_registry.py`
The Kenyan vehicle physics registry. Defines two dataclasses — `StructuralZones` and `VehicleProfile` — and a dictionary (`VEHICLE_REGISTRY`) containing physics profiles for every vehicle model commonly seen in Kenyan motor insurance claims (Toyota Premio, Hiace matatus, Isuzu trucks, Bajaj bodas, semi-trailers, etc.).

Each profile stores the vehicle's kerb weight, gross weight, passenger load, dimensions, safety features (ABS, airbags, ESC), McHenry stiffness coefficients (`crumple_A`, `crumple_B`), centre-of-gravity ratio, and structural zone coordinates used for 3D impact vertex mapping.

`FALLBACK_PROFILES` provides generic profiles by body type when a specific make/model is not in the registry. The lookup function `get_vehicle_profile()` tries exact match → partial match → body type fallback → generic saloon, returning both the profile and the method used.

**All data is currently DUMMY/ESTIMATED** pending Old Mutual submitting actual fleet data.

---

### `seed_claims_db.py`
A one-time database seeding script. Run this once (or re-run to refresh) to populate two lookup tables into `claims_database.db`:

`friction_mu_lookup` — friction coefficients (μ) for every road type × weather combination seen in Kenyan claims (asphalt/dry, murram/wet, cobblestone/wet, etc.). These μ values feed directly into the braking distance and speed calculations in the physics engine.

`vehicle_registry` (DB table) — mirrors `vehicle_registry.py` but in SQLite form, used by `registry_adapter.py` for runtime DB queries.

`vehicle_class_mapping` — maps body types (`saloon`, `suv`, `matatu`, etc.) to the physics engine's internal stiffness bucket labels.

Run with: `python seed_claims_db.py`

---

### `registry_adapter.py`
Bridges `claims_database.db` to `ConfigurableMultiVehicleEngine`. Instead of hardcoding vehicle specs in the engine, this adapter queries the DB at runtime with a four-tier fallback (exact key → partial match → body type → generic saloon).

`get_vehicle_spec()` returns an engine-compatible dict including mass, stiffness coefficients, and safety features.

`get_friction_mu()` returns the friction coefficient for a given road type and weather condition, with fallback to wet conditions then to a default of 0.45.

`resolve_payload()` is the main entry point — it takes a raw simulation payload, enriches it with DB-resolved vehicle specs and friction mu, and returns it ready for `ConfigurableMultiVehicleEngine`.

---

### `physics_engine.py`
The core crash reconstruction engine. Given two vehicles and their stated conditions, it derives physics-based estimates of what actually happened and scores the discrepancy as a fraud signal.

Key functions:

`mchenry_crush_energy()` — implements the McHenry Crush Energy Model: converts observed crush depth and surface width into absorbed energy (joules) using the vehicle's stiffness coefficients A and B.

`velocity_from_crush_energy()` — inverts the energy equation to derive the speed a vehicle must have been travelling to produce the observed crush damage.

`expected_crush_depth()` — given a stated speed, predicts what the crush depth *should* be, for comparison against observed damage.

`braking_distance()` / `speed_at_impact()` — Newton-Euler braking model accounting for road slope, friction, and ABS.

`momentum_analysis()` — applies conservation of momentum with a semi-plastic restitution coefficient to compute post-impact velocities and delta-V for both vehicles.

`velocity_fraud_severity()` — classifies the gap between stated and physics-derived speed as none / low / medium / high / critical.

`CrashReconstructionEngine.reconstruct()` — the main reconstruction method. It runs all of the above in sequence, appends inconsistency flags (crush depth mismatch, implausible speed for location, mass ratio anomalies), computes a 0–100 fraud score, and returns a `PhysicsResult`.

`reconstruct_from_input()` — wraps `reconstruct()` and adds Pathway 1 enhancements: stores 3D impact vertex, stores force magnitude, and re-evaluates fraud severity using GPS telemetry delta-V when available (more reliable than crush energy inference).

`PhysicsResult` — the dataclass that carries all outputs: speeds, crush depths, delta-V, inconsistency list, fraud score, verdict (CONSISTENT / SUSPICIOUS / INCONSISTENT), SHA-256 signature, and an explanation string for investigators.

`build_physics_explanation()` — generates a plain-English investigator report from a `PhysicsResult`, explaining how the score was derived without mathematical notation.

---

### `pipeline_bridge.py`
The entry point and traffic controller for all reconstruction requests. Its job is to normalise raw inbound payloads into a standard `PhysicsInput` structure before handing off to `physics_engine.py`.

`PhysicsInput` — the standardised dataclass consumed by the physics engine regardless of whether the claim came from a Flutter telemetry app or a manual narrative form.

`determine_pathway()` — decides between Pathway 1 (precise: telemetry or vision 3D data present) and Pathway 2 (fallback: narrative only).

`process_telemetry_payload()` — handles Pathway 1. Validates HMAC-SHA256 signature for chain of custody, maps impact zone label to `[X, Y, Z]` vehicle structural coordinates, computes a 3D force vector from accelerometer + gyroscope data, derives crush depth from telemetry when not explicitly provided, and overrides stated speed with GPS speed when HMAC is valid.

`process_manual_payload()` — handles Pathway 2. Infers all physics parameters from narrative text using keyword dictionaries for speed (`SPEED_KEYWORDS`), damage severity (`DAMAGE_KEYWORDS`), impact zone (`IMPACT_ZONE_KEYWORDS`), and collision angle (`COLLISION_ANGLE_KEYWORDS`). Each inference includes a confidence score.

`data_quality_score()` — scores the completeness of a `PhysicsInput` on a 0–1 scale based on how many fields were provided vs inferred.

---

### `pixel_to_3d_transformer.py`
Converts vehicle damage photographs into real-world 3D measurements for use in Pathway 1 reconstruction.

The pipeline is: extract EXIF camera parameters → call Gemini Vision to detect the damage region (bounding box + impact zone + depth label) → apply perspective camera geometry to convert pixel measurements to millimetres → map impact zone to `[X, Y, Z]` vehicle structural coordinates.

`extract_camera_params()` — reads focal length and sensor dimensions from image EXIF. Falls back to typical Kenyan smartphone defaults (4.2mm focal length, 1/2.55" sensor) when EXIF is unavailable.

`compute_fov()` / `pixels_to_mm()` — perspective projection geometry: `real_size = px × (2 × d × tan(FOV/2)) / image_width`.

`_call_gemini_vision()` — sends the photo to Gemini 1.5 Flash with a structured forensic damage analysis prompt. Returns a JSON object with damage bounding box, impact zone, crush depth label (none/minimal/shallow/moderate/deep/severe/catastrophic), and estimated camera distance.

`DEPTH_LABEL_MAP` — maps Gemini's qualitative depth labels to mm ranges. The midpoint of the range is used as the crush depth input to the physics engine.

`PixelTo3DTransformer.transform()` — the full pipeline returning a `TransformResult` with `vision_vertex_xyz`, `crush_depth_mm`, `crush_width_mm`, overall confidence, and a Gemini raw audit trail.

`transform_to_bridge_payload()` — convenience wrapper that returns a dict ready to be merged directly into a `pipeline_bridge` payload, activating Pathway 1 via `vision_vertex_xyz`.

---

### `configurable_multi_vehicle_engine.py`
Generates a frame-by-frame telemetry timeline of a two-vehicle collision. Takes a payload (with vehicle registry data and environment snapshot) and produces position, velocity, and collision state for both vehicles across a configurable pre-crash and post-crash window.

The timeline has three phases: normal cruise (constant velocity approach) → PRT phase (perception-reaction time, foot not yet on brake) → active braking → impact at t=0 → post-impact deceleration.

Uses variable time steps: 0.5s macro steps during normal cruise, 0.05s micro steps through the impact zone for detail.

This timeline feeds `render_simulation_video.py` and is also stored in the claim record for the admin full-report endpoint.

---

### `render_simulation_video.py`
Renders the collision simulation as an MP4 video suitable for insurance investigator reports.

Takes the timeline output from `ConfigurableMultiVehicleEngine` and animates it using Matplotlib's `FuncAnimation` with `FFMpegWriter`. The 7-second video shows a top-down road view with both vehicles approaching, the impact flash, skid marks, post-impact trajectories, real-time speed labels, a physics HUD overlay, and a progress bar.

The video is keyed to a specific claim ID and watermarked "For investigative use only". It is stored to disk and its path is written to the `simulation_video_path` column in the claims database.

Requires FFmpeg to be installed on the server. The rendering runs synchronously and takes a few seconds per video at 150 DPI / 30 FPS.

---

### `reconstruction_routes.py`
FastAPI router (`/api/reconstruction`) exposing the physics reconstruction engine to internal API consumers.

`POST /manual` — runs a full Pathway 2 reconstruction from form fields (narrative, vehicle details, location, crush depth). Returns the complete `PhysicsResult` dict.

`POST /telemetry` — runs a Pathway 1 reconstruction from a Flutter app JSON payload. Validates HMAC, processes telemetry, returns result with `hmac_valid` flag.

`GET /verdict/{claim_id}` — retrieves a previously stored physics verdict from the DB without re-running reconstruction.

`GET /vehicle-registry` — lists all vehicles in the physics registry with their confidence scores.

`GET /vehicle-lookup` — looks up the physics profile the engine will use for a given make/model/body_type combination. Useful for debugging why a claim used a fallback profile.

`GET /health` — reports whether PyBullet (optional full multibody solver) is available or the analytical Newton-Euler solver is active.

---

### `routes.py`
The main FastAPI application router, covering three routers: `claims_router` (`/api/claims`), `analysis_router` (`/api/analysis`), and `system_router` (`/api/system`).

**Submission endpoints (party-facing — no fraud results shown):**

`POST /api/analysis/member` — member submits narrative, estimated cost, location, and photos. Stores photos to DB, runs full backend analysis via `ClaimOrchestrator.analyze_multiparty_claim()`, stores the result, and returns only an acknowledgment with next-steps guidance. The fraud score is intentionally hidden.

`POST /api/analysis/assessor` — assessor submits damage report and inspection photos. Retrieves member's prior photos from DB, re-runs analysis with both parties' data combined, stores updated result. Returns acknowledgment only.

`POST /api/analysis/repair-shop` — repair shop submits estimate and photos. Retrieves all prior photos, runs the final combined analysis across all three parties, stores the definitive result. Returns acknowledgment only.

**Video endpoints:**

`GET /api/analysis/claims/{claim_id}/simulation-video` — serves the MP4 collision simulation as a file download. Internal/assessor use only.

`GET /api/analysis/claims/{claim_id}/simulation-status` — polls whether the simulation video is ready, without downloading it. For dashboard polling.

**Admin/SIU endpoints (full fraud results):**

`GET /api/analysis/claim/{claim_id}/full-report` — the primary investigator endpoint. Returns the complete fraud picture: cross-party verification, all photo anomalies and narrative inconsistencies, risk breakdown by component, physics reconstruction summary, final decision (APPROVE / INVESTIGATE / DECLINE) with reasoning, and next-action steps. Accepts `include_timeline=true` to embed the full 39-frame simulation data.

`GET /api/analysis/claim/{claim_id}/summary` — condensed one-screen summary for a claim dashboard: decision icon (🟢/🟡/🔴), fraud score, cross-party issues, duplicate photo count, party completion status.

`GET /api/analysis/claims/all` — paginated list of all claims with the same full-report detail level. Supports filtering by `risk_level`, `physics_verdict`, and `decision`.

`GET /api/analysis/status/{claim_id}` — shows which parties have submitted and what the next expected step is.

**System endpoints:**

`GET /api/system/health` — database connectivity and service status.

`GET /api/system/stats` — aggregate fraud detection metrics.

`GET /api/system/metrics/detailed` — performance breakdown with p95/p99 processing times and risk distribution.

`GET /api/system/database/status` — table row counts and DB file size.

---

## Dual-Pathway Explained

The system routes every claim through one of two reconstruction pathways:

**Pathway 1 (Precise)** activates when telemetry data (GPS speed + accelerometer peak G) or vision-derived 3D data (`vision_vertex_xyz` from `pixel_to_3d_transformer.py`) is present. It uses objective sensor measurements to derive delta-V and override narrative-inferred speeds. Confidence is boosted by +0.15. This pathway is ready for a Flutter mobile app integration that has not yet been deployed.

**Pathway 2 (Narrative)** is the current production pathway for all existing claims. It infers all physics inputs from claim text using keyword dictionaries and regex patterns. Confidence is lower (0.25–0.85 depending on specificity of language) but sufficient to detect significant fraud signals.

---

## Fraud Scoring

The final `physics_fraud_score` (0–100) is computed as:

```
score = (velocity_penalty × 0.6) + (consistency_penalty × 0.4)
```

Velocity penalties by severity: none=0, low=5, medium=20, high=40, critical=65.

Consistency penalties accumulate from: crush depth mismatch (+25), velocity inconsistency (+30/+50), mass ratio anomaly (+15), implausible speed for location (+20).

Verdicts: score < 20 → CONSISTENT, score < 45 → SUSPICIOUS, score ≥ 45 → INCONSISTENT.

Final claim decisions layer on top of the physics score: any CRITICAL photo anomaly or INCONSISTENT physics verdict forces INVESTIGATE regardless of the raw score.

---

## Database

The system uses SQLite (`claims_database.db`). Key tables:

- `claims` — primary claim records with fraud scores, verdicts, analysis JSON, and simulation video path.
- `claim_photos` — binary photo storage keyed by claim ID and party (member / assessor / repair_shop).
- `vehicle_registry` — seeded vehicle physics profiles (from `seed_claims_db.py`).
- `friction_mu_lookup` — friction coefficients by road type and weather.
- `vehicle_class_mapping` — body type to engine stiffness class mapping.
- `system_metrics` — aggregate performance tracking.
- `processing_logs` — per-claim processing audit trail.

---

## Key Dependencies

- `fastapi` + `uvicorn` — API server
- `numpy`, `scipy` — physics calculations
- `matplotlib` — simulation video rendering
- `ffmpeg` (system) — video encoding (must be installed on the server)
- `google-generativeai` — Gemini Vision for photo damage analysis
- `Pillow` — EXIF extraction from photos
- `pybullet` (optional) — full multibody physics solver; falls back to analytical Newton-Euler if not installed
- `sqlite3` (stdlib) — database

---

## Setup

1. Install Python dependencies.
2. Install FFmpeg on the server (`sudo apt install ffmpeg`).
3. Run `python seed_claims_db.py` to initialise the vehicle registry and friction tables.
4. Mount `reconstruction_router` and the routers from `routes.py` into `main.py`.
5. Replace all `DUMMY` vehicle profiles in `vehicle_registry.py` and `seed_claims_db.py` with real Old Mutual fleet data when available.

---

## Outstanding Items

- All vehicle physics profiles are **DUMMY data** pending Old Mutual fleet submission. Confidence scores reflect this (0.4–0.72). Real measured or manufacturer data will improve reconstruction accuracy significantly.
- The Flutter mobile telemetry app (Pathway 1) is not yet deployed. The `process_telemetry_payload()` and HMAC validation infrastructure is fully built and waiting.
- `physics_explanation` on `PhysicsResult` is populated by Gemini in `service.py` using `build_physics_explanation()` as a fallback. Ensure the Gemini API key is set in the environment.
- The `ClaimOrchestrator` in `service.py` (not in this file set) orchestrates the full multi-party analysis pipeline including photo anomaly detection, narrative NLP, cross-party verification, and risk scoring. The files in this set are the physics reconstruction module that `ClaimOrchestrator` calls into.