// Shared shapes for the interactive physics reconstruction viewer.
// These mirror the REAL fields the backend actually returns (routes.py's
// `physics_reconstruction` object and service.py's `comparison`/telemetry
// output) -- nothing here is aspirational. Fields that only exist on
// Pathway 1 (telemetry) claims are typed optional/nullable so callers are
// forced to handle their absence rather than assuming they're always there.

export type TelemetryFrame = {
  time_sec: number;
  position: [number, number, number];
  velocity_kmh: number;
  is_colliding?: boolean;
};

export type SimulationTimeline = {
  claim_id?: string;
  simulation_metadata?: {
    delta_v1_kmh?: number;
    delta_v2_kmh?: number;
    v1_pre_impact_speed_kmh?: number;
    v2_pre_impact_speed_kmh?: number;
    friction_mu?: number;
    [key: string]: any;
  };
  v1_insured_telemetry: TelemetryFrame[];
  v2_third_party_telemetry: TelemetryFrame[];
};

export type ComparisonField = { claimed?: number | null; reconstructed?: number | null; delta?: number | null };

export type PhysicsComparison = {
  speed_v1_kmh?: ComparisonField;
  speed_v2_kmh?: ComparisonField;
  crush_depth_mm?: ComparisonField;
  velocity_fraud_flag?: boolean;
  velocity_fraud_severity?: "none" | "low" | "medium" | "high" | "critical";
  impact_consistency_score?: number;
  v1_speed_is_inferred?: boolean;
  v1_speed_confidence?: number;
  v2_speed_is_inferred?: boolean;
  v2_speed_confidence?: number;
  approach_angle_deg?: number;
  data_quality_score?: number;
};

export type PhysicsDataSources = {
  crush_depth?: "assessor_measured" | "llm_extracted" | "vision_estimated" | "not_available";
  v1_speed?: "stationary_override" | "llm_extracted" | "keyword_inferred";
  v2_speed?: "stationary_override" | "llm_extracted" | "keyword_inferred";
  approach_angle?: "assessor_measured" | "llm_extracted" | "keyword_inferred";
  v1_vehicle?: "llm_extracted" | "keyword_inferred";
  v2_vehicle?: "llm_extracted" | "keyword_inferred";
  mchenry_ran?: boolean;
};

export type PhysicsInconsistency = { type?: string; severity?: string; description?: string; confidence?: number };

export type PhysicsReconstruction = {
  status: "not_run" | "skipped" | "complete" | string;
  claim_category?: string;
  pathway?: "pathway_1" | "pathway_2" | string;
  vehicle_1_key?: string | null;
  vehicle_2_key?: string | null;
  // vehicle_2_key is a human-readable string ("Fixed Object Barrier
  // (body_type_fallback)"), not something a "is this a barrier" check can
  // reliably parse back out of -- use this field directly instead.
  v2_body_type?: string | null;
  physics_fraud_score?: number;
  physics_verdict?: "CONSISTENT" | "SUSPICIOUS" | "INCONSISTENT" | string;
  verdict_reason?: string;
  physics_explanation?: string;
  simulation_method?: "analytical" | "pybullet" | string;
  confidence?: number;
  inconsistencies?: PhysicsInconsistency[];
  warnings?: string[];
  signature?: string;
  timeline_metadata?: SimulationTimeline["simulation_metadata"];
  timeline?: SimulationTimeline | null;
  comparison?: PhysicsComparison;
  data_sources?: PhysicsDataSources;
  // Pathway-1-only fields -- null/undefined on every narrative-pathway claim.
  delta_v_kmh?: number | null;
  kinetic_energy_j?: number | null;
  crush_energy_j?: number | null;
  energy_consistent?: boolean | null;
  impact_force_magnitude_n?: number | null;
  // True for every claim except Pathway 1 (telemetry) ones -- the number is
  // still real physics (impulse-momentum: mass x Δv / an assumed crash-
  // pulse duration), just not measured from real sensor data, so it must be
  // labeled differently from a bare "CALCULATED" figure.
  impact_force_is_estimated?: boolean | null;
  v1_impact_vertex_xyz?: [number, number, number] | null;
  terrain_adjusted?: boolean | null;
  slope_adjustment_kmh?: number | null;
  // Structural zone actually used for this reconstruction (front_bumper/
  // rear_bumper/driver_door/passenger_door/rear_driver/rear_passenger/roof).
  // When `impact_zone_v1_source` is "cv_detected", it came from the claim's
  // own photos (part_identifier.py's damage_zones), not a narrative guess --
  // `impact_zone_v1_detected_part` carries the original fine-grained part
  // name (e.g. "Front Left Door") for display.
  impact_zone_v1?: string | null;
  impact_zone_v1_source?: "cv_detected" | "claimant_stated" | "narrative_inferred" | null;
  impact_zone_v1_detected_part?: string | null;
  measurement_flags?: string[];
  has_measurement_discrepancy?: boolean;
};

export type VehicleBodyType =
  | "saloon" | "suv" | "pickup" | "matatu" | "motorcycle" | "tuk_tuk" | "heavy_commercial" | string;

export type VehicleInfo = {
  make?: string;
  model?: string;
  body_type?: VehicleBodyType;
  length_m?: number;
  width_m?: number;
};

export type DamageZoneWithParty = {
  part: string;
  damage_type: string;
  severity: string;
  confidence: number;
  description: string;
  bbox_normalized: number[] | null;
  vehicle?: string | null;
  party?: string;
  photoFilename?: string;
};

export type VisibleLayers = {
  trajectories: boolean;
  velocityVectors: boolean;
  impactForce: boolean;
  skidMarks: boolean;
  impactZone: boolean;
  grid: boolean;
  labels: boolean;
  damageOverlay: boolean;
};

export const DEFAULT_LAYERS: VisibleLayers = {
  trajectories: true,
  velocityVectors: true,
  impactForce: true,
  skidMarks: true,
  impactZone: true,
  grid: true,
  labels: true,
  damageOverlay: false,
};

export type CameraMode = "overview" | "approach" | "impact" | "v1" | "v2" | "free";

/** Fallback vehicle label when no dedicated make/model source is wired up
 * yet. `vehicle_1_key`/`vehicle_2_key` are NOT a plain lookup key like
 * "toyota_premio" -- physics_engine.py's reconstruct() actually sets them to
 * a human-readable string with a parenthetical lookup-method suffix, e.g.
 * "Toyota Premio (db_exact_match)" or "Fixed Object Barrier
 * (body_type_fallback)". Splitting that on "_" (as this function previously
 * did) shredded it into garbage like "V1 ...Match)". Strip the "(...)"
 * suffix and use the plain text before it instead. */
export function vehicleInfoFromKey(key?: string | null): VehicleInfo | undefined {
  if (!key) return undefined;
  const withoutSuffix = key.replace(/\s*\([^)]*\)\s*$/, "").trim();
  if (!withoutSuffix) return undefined;
  const [make, ...rest] = withoutSuffix.split(" ");
  return { make, model: rest.join(" ") };
}

export type SimEvent = {
  key: string;
  label: string;
  t: number;
  vehicle?: "V1" | "V2" | "both";
  detail?: string;
};
