// Overridable via VITE_API_BASE_URL in a .env.local file so the devserver
// deployment doesn't need this source-level value hand-patched (and
// silently reverted) every time this file gets synced from a local dev
// checkout. .env.local is gitignored/never synced -- set it once on the
// devserver and it survives every future api.ts upload.
export const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8010";

export async function transcribeAudio(audioBlob: Blob, filename: string): Promise<{
  success: boolean;
  text?: string;
  error?: string;
}> {
  const formData = new FormData();
  formData.append("audio", audioBlob, filename);
  const res = await fetch(`${BASE_URL}/api/analysis/speech-to-text`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export async function getMemberDetails(memberId: string) {
  const res = await fetch(`${BASE_URL}/api/member/${memberId}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function getMemberPolicies(memberId: string) {
  const res = await fetch(`${BASE_URL}/api/member/${memberId}/policies`, { headers: { accept: "application/json" } });
  return res.json();
}

export type MemberSearchResult = { member_id: string; name: string; email: string; phone: string };

export async function searchMembers(query: string): Promise<{ success: boolean; results: MemberSearchResult[] }> {
  const res = await fetch(`${BASE_URL}/api/members/search?q=${encodeURIComponent(query)}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function createMember(data: { name: string; email?: string; phone?: string }): Promise<{ success: boolean; member_id?: string; detail?: string }> {
  const body = new URLSearchParams();
  body.append("name", data.name);
  if (data.email) body.append("email", data.email);
  if (data.phone) body.append("phone", data.phone);
  const res = await fetch(`${BASE_URL}/api/members/create`, {
    method: "POST", headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" }, body,
  });
  return res.json();
}

export async function createPolicy(data: {
  member_id: string; policy_number?: string; cover_type?: string; sum_insured: number; excess?: number;
  start_date: string; end_date: string;
  vehicle_make?: string; vehicle_model?: string; vehicle_year?: number; vehicle_reg_no?: string;
}): Promise<{ success: boolean; policy_id?: string; detail?: string }> {
  const body = new URLSearchParams();
  Object.entries(data).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") body.append(k, String(v)); });
  const res = await fetch(`${BASE_URL}/api/policies/create`, {
    method: "POST", headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" }, body,
  });
  return res.json();
}

export async function getAnalystClaims(analystId: string) {
  const res = await fetch(`${BASE_URL}/analyst-claims?analyst_id=${encodeURIComponent(analystId)}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function lookupClaim(claimId: string): Promise<{
  success: boolean;
  claim_id?: string;
  member_id?: string;
  member_name?: string | null;
  location?: string;
  estimated_cost?: number;
  created_at?: string;
  detail?: string;
}> {
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${encodeURIComponent(claimId)}/lookup`, { headers: { accept: "application/json" } });
  return res.json();
}

export type ClaimListEntry = {
  claim_id: string;
  member_id: string | null;
  member_name: string | null;
  location: string | null;
  created_at: string | null;
};

export async function listAllClaims(): Promise<{ success: boolean; claims: ClaimListEntry[]; total: number }> {
  const res = await fetch(`${BASE_URL}/api/analysis/claims/list`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function checkCoverage(data: {
  member_id: string;
  claim_type: string;
  incident_date: string;
  incident_location: string;
  driver_name?: string;
  brief_description: string;
}) {
  const body = new URLSearchParams();
  Object.entries(data).forEach(([k, v]) => { if (v) body.append(k, v); });
  const res = await fetch(`${BASE_URL}/check-coverage`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}

export async function createClaim(data: {
  coverage_check_id: string;
  member_id: string;
  policy_id: string;
  incident_date: string;
  incident_location: string;
  brief_description: string;
  claim_type: string;
  filed_by_analyst_id?: string;
  filing_method?: "phone" | "form";
}) {
  const body = new URLSearchParams();

  // Only append the specific keys the backend expects
  const requiredKeys = [
    "coverage_check_id",
    "member_id",
    "policy_id",
    "incident_date",
    "incident_location",
    "brief_description",
    "claim_type",
    "filed_by_analyst_id",
    "filing_method",
  ];

  requiredKeys.forEach(key => {
    const value = data[key as keyof typeof data];
    // Trim and ensure we aren't sending "undefined" as a string
    if (value !== undefined && value !== null) {
      body.append(key, String(value).trim());
    }
  });

  const res = await fetch(`${BASE_URL}/create-claim`, {
    method: "POST",
    headers: { 
      accept: "application/json", 
      "Content-Type": "application/x-www-form-urlencoded" 
    },
    body,
  });
  return res.json();
}

export async function ensureOllamaReady(): Promise<{ success: boolean; status: string; actions_taken?: string[] }> {
  const res = await fetch(`${BASE_URL}/api/system/ensure-ollama-ready`, {
    method: "POST",
    headers: { accept: "application/json" },
  });
  return res.json();
}

export type ExtractedIntakeFacts = {
  third_party_involved?: "Yes" | "No" | null;
  third_party_summary?: string | null;
  police_reported?: "Yes" | "No" | null;
  witnesses_present?: "Yes" | "No" | null;
  injuries_reported?: "Yes" | "No" | null;
};

export async function assessNarrative(data: {
  narrative: string;
  claim_type?: string;
  round?: number;
}): Promise<{
  sufficient: boolean;
  clarifying_question: string | null;
  missing_aspect: string | null;
  extracted_facts?: ExtractedIntakeFacts;
}> {
  const body = new URLSearchParams();
  body.append("narrative", data.narrative);
  if (data.claim_type) body.append("claim_type", data.claim_type);
  body.append("round", String(data.round ?? 0));
  const res = await fetch(`${BASE_URL}/api/analysis/intake/assess-narrative`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}

export async function submitMemberClaim(data: {
  claim_id: string;
  member_id: string;
  narrative: string;
  estimated_cost: number;
  location: string;
  incident_date: string;
  photos: File[];
  third_party_involved?: string;
  third_party_details?: string;
  third_party_fled?: string;
  other_vehicle_position?: string;
  police_reported?: string;
  police_ob_number?: string;
  witnesses_present?: string;
  witness_details?: string;
  injuries_reported?: string;
  injury_details?: string;
  id_document?: File;
}) {
  const formData = new FormData();
  formData.append("claim_id", data.claim_id);
  formData.append("member_id", data.member_id);
  formData.append("narrative", data.narrative);
  formData.append("estimated_cost", String(data.estimated_cost));
  formData.append("location", data.location);
  formData.append("incident_date", data.incident_date);
  const structuredFields: (keyof typeof data)[] = [
    "third_party_involved", "third_party_details", "third_party_fled",
    "other_vehicle_position",
    "police_reported", "police_ob_number",
    "witnesses_present", "witness_details",
    "injuries_reported", "injury_details",
  ];
  structuredFields.forEach((key) => {
    const value = data[key];
    if (typeof value === "string") formData.append(key, value);
  });
  data.photos.forEach((p) => formData.append("photos", p));
  if (data.id_document) formData.append("id_document", data.id_document);
  const res = await fetch(`${BASE_URL}/api/analysis/member`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export async function uploadDocument(data: {
  claimId: string;
  party: "member" | "assessor";
  documentType: "police_abstract" | "id_document" | "garage_quote" | "claim_form" | "other";
  uploaderId: string;
  file: File;
}): Promise<{
  success: boolean;
  document_id?: number;
  parsed_fields?: Record<string, unknown>;
  extraction_confidence?: number;
  detail?: string;
}> {
  const formData = new FormData();
  formData.append("claim_id", data.claimId);
  formData.append("party", data.party);
  formData.append("document_type", data.documentType);
  formData.append("uploader_id", data.uploaderId);
  formData.append("file", data.file);
  const res = await fetch(`${BASE_URL}/api/analysis/documents/upload`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export type ClaimFormFields = {
  policy_no: string | null;
  branch: string | null;
  cover_type: string | null;
  insured_full_name: string | null;
  insured_id_no: string | null;
  insured_phone: string | null;
  insured_email: string | null;
  insured_address: string | null;
  vehicle_make: string | null;
  vehicle_model: string | null;
  vehicle_year: string | null;
  vehicle_reg_no: string | null;
  registered_owner_name: string | null;
  accident_date: string | null;
  accident_time: string | null;
  accident_place: string | null;
  road_surface: string | null;
  weather_condition: string | null;
  damage_description: string | null;
  repairer_name: string | null;
  repairer_address: string | null;
  repairer_phone: string | null;
  vehicle_still_in_use: boolean | null;
  police_involved: boolean | null;
  police_station: string | null;
  police_constable_number: string | null;
  third_party_vehicles: { owner_name?: string; reg_no?: string; insurer?: string }[];
  third_party_property_damaged: { owner_name?: string; property_damaged?: string }[];
  persons_injured: { name?: string; relationship_to_insured?: string; apparent_injuries?: string }[];
  witnesses: { name?: string; address?: string }[];
  driver_name: string | null;
  driver_relationship_to_insured: string | null;
  driver_employed_by_insured: boolean | null;
  driver_had_permission: boolean | null;
  driver_to_blame: boolean | null;
  driver_admitted_liability: boolean | null;
  driver_licence_number: string | null;
  driver_statement: string | null;
  owner_statement: string | null;
  declaration_date: string | null;
};

export async function extractClaimForm(files: File[], analystId: string): Promise<{
  success: boolean;
  parsed_fields: ClaimFormFields;
  extraction_confidence: number;
  document_appears_genuine: boolean | null;
  quality_notes: string;
  detail?: string;
}> {
  const formData = new FormData();
  formData.append("analyst_id", analystId);
  files.forEach((f) => formData.append("files", f));
  const res = await fetch(`${BASE_URL}/api/analysis/claim-form/extract`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export type ClaimPhoto = {
  id: number;
  claim_id?: string;
  filename: string;
  party: string;
  file_size: number;
  content_type: string;
  uploaded_at: string;
  uploaded_by?: string | null;
};

export async function getClaimPhotos(claimId: string, party?: string): Promise<{ success: boolean; photos: ClaimPhoto[] }> {
  const params = party ? `?party=${encodeURIComponent(party)}` : "";
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/photos${params}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function addClaimPhotos(data: {
  claimId: string;
  uploaderType: "member" | "analyst";
  uploaderId: string;
  photos: File[];
}): Promise<{ success: boolean; photos_added?: number; detail?: string }> {
  const formData = new FormData();
  formData.append("uploader_type", data.uploaderType);
  formData.append("uploader_id", data.uploaderId);
  data.photos.forEach((p) => formData.append("photos", p));
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${data.claimId}/photos`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

// Kept in lockstep with MEMBER_FIELD_LABELS in routes.py -- the only claim
// fields a member can be asked to fill in when the paper form left them blank.
export const MEMBER_FIELD_LABELS: Record<string, string> = {
  estimated_cost: "Estimated Repair Cost (KES)",
  location: "Incident Location",
  narrative: "What Happened (narrative)",
};

export async function notifyMemberToAddPhotos(claimId: string, requestedBy: string, missingFields?: string[]): Promise<{
  success: boolean;
  sent_to?: string;
  reason?: string;
}> {
  const body = new URLSearchParams();
  body.append("requested_by", requestedBy);
  if (missingFields && missingFields.length > 0) {
    body.append("missing_fields", JSON.stringify(missingFields));
  }
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/notify-member`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}

export async function submitMemberFieldAnswers(claimId: string, memberId: string, fields: Record<string, string | number>): Promise<{
  success: boolean;
  updated?: string[];
  pending_member_fields?: string[];
  detail?: string;
}> {
  const body = new URLSearchParams();
  body.append("member_id", memberId);
  body.append("fields", JSON.stringify(fields));
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/member-update-fields`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}

export async function getAssessorClaims(assessorId: string, status?: string) {
  const params = new URLSearchParams({ assessor_id: assessorId });
  if (status) params.append("status", status);
  const res = await fetch(`${BASE_URL}/my-claims?${params}`, { headers: { accept: "application/json" } });
  return res.json();
}

export type AssessorDashboardOverview = {
  success: boolean;
  total_claims: number;
  pending_inspections: number;
  inspections_scheduled_today: number;
  reports_awaiting_submission: number;
  reports_returned_for_review: number;
  completed_assessments: number;
  overdue_assessments: number;
  avg_turnaround_hours: number | null;
  estimated_claim_value_total: number;
  estimated_claim_value_avg: number;
  claims_by_status: Record<string, number>;
};

export async function getAssessorDashboardOverview(assessorId: string): Promise<AssessorDashboardOverview> {
  const res = await fetch(`${BASE_URL}/api/assessor/${assessorId}/dashboard-overview`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function returnAssignmentForReview(data: { assignmentId: string; reason: string; returnedBy: string }): Promise<{ success: boolean; detail?: string }> {
  const formData = new FormData();
  formData.append("reason", data.reason);
  formData.append("returned_by", data.returnedBy);
  const res = await fetch(`${BASE_URL}/api/assessors/assignment/${data.assignmentId}/return-for-review`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export type DamageDetection = {
  class: string;
  component: string;
  confidence: number;
  bbox: number[];
  polygon?: number[][];
  recommended_action: "repair" | "replace";
  reason: string;
  low_confidence?: boolean;
};

export type DamageZone = {
  part: string;
  damage_type: string;
  severity: "minor" | "moderate" | "severe";
  confidence: number;
  description: string;
  bbox_normalized: number[] | null;
  recommended_action: "repair" | "replace";
};

export async function saveDamageDecision(data: {
  claimId: string;
  filename: string;
  detectionIndex: number;
  component: string;
  aiRecommendation: string;
  assessorDecision: "repair" | "replace";
  assessorId: string;
}): Promise<{ success: boolean; detail?: string }> {
  const formData = new FormData();
  formData.append("claim_id", data.claimId);
  formData.append("filename", data.filename);
  formData.append("detection_index", String(data.detectionIndex));
  formData.append("component", data.component);
  formData.append("ai_recommendation", data.aiRecommendation);
  formData.append("assessor_decision", data.assessorDecision);
  formData.append("assessor_id", data.assessorId);
  const res = await fetch(`${BASE_URL}/api/analysis/photos/damage-decision`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export async function getDamageDecisions(claimId: string): Promise<{
  success: boolean;
  decisions: { filename: string; detection_index: number; component: string; ai_recommendation: string; assessor_decision: string; assessor_id: string; decided_at: string }[];
}> {
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/damage-decisions`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function getClaimDetails(claimId: string, assessorId: string) {
  const res = await fetch(`${BASE_URL}/claim-details/${claimId}?assessor_id=${assessorId}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function getMemberClaimDetails(claimId: string, memberId: string) {
  const res = await fetch(`${BASE_URL}/member-claim-details/${claimId}?member_id=${memberId}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function correctDocumentField(data: {
  documentId: number;
  correctedFields: Record<string, unknown>;
  correctedBy: string;
}) {
  const body = new URLSearchParams();
  body.append("corrected_fields", JSON.stringify(data.correctedFields));
  body.append("corrected_by", data.correctedBy);
  const res = await fetch(`${BASE_URL}/api/analysis/documents/${data.documentId}/correct`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}

export async function scheduleInspection(data: {
  assignment_id: string;
  inspection_date: string;
  location?: string;
  notes?: string;
}) {
  const body = new URLSearchParams();
  Object.entries(data).forEach(([k, v]) => { if (v) body.append(k, v); });
  const res = await fetch(`${BASE_URL}/schedule-inspection`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}

export async function submitAssessorReport(data: {
  claim_id: string;
  assessor_id: string;
  damage_report: string;
  estimated_cost: number;
  inspection_date: string;
  photos: File[];
  crush_depth_mm?: number;
  approach_angle_deg?: number;
  third_party_vehicle_confirmed?: string;
  garage_quote?: File;
  id_document?: File;
}) {
  const formData = new FormData();
  formData.append("claim_id", data.claim_id);
  formData.append("assessor_id", data.assessor_id);
  formData.append("damage_report", data.damage_report);
  formData.append("estimated_cost", String(data.estimated_cost));
  formData.append("inspection_date", data.inspection_date);
  if (data.crush_depth_mm !== undefined) formData.append("crush_depth_mm", String(data.crush_depth_mm));
  if (data.approach_angle_deg !== undefined) formData.append("approach_angle_deg", String(data.approach_angle_deg));
  if (data.third_party_vehicle_confirmed) formData.append("third_party_vehicle_confirmed", data.third_party_vehicle_confirmed);
  data.photos.forEach((p) => formData.append("photos", p));
  if (data.garage_quote) formData.append("garage_quote", data.garage_quote);
  if (data.id_document) formData.append("id_document", data.id_document);
  const res = await fetch(`${BASE_URL}/api/analysis/assessor`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export async function submitRepairShopEstimate(data: {
  claim_id: string;
  shop_id: string;
  repair_estimate: string;
  total_cost: number;
  estimate_date: string;
  photos: File[];
}) {
  const formData = new FormData();
  formData.append("claim_id", data.claim_id);
  formData.append("shop_id", data.shop_id);
  formData.append("repair_estimate", data.repair_estimate);
  formData.append("total_cost", String(data.total_cost));
  formData.append("estimate_date", data.estimate_date);
  data.photos.forEach((p) => formData.append("photos", p));
  const res = await fetch(`${BASE_URL}/api/analysis/repair-shop`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return res.json();
}

export async function getAdminClaims(params: {
  limit?: number;
  offset?: number;
  risk_level?: string;
  verdict?: string;
  decision?: string;
}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { 
    if (v !== undefined && v !== null) query.append(k, String(v)); 
  });
  const res = await fetch(`${BASE_URL}/api/analysis/claims/all?${query}`, { 
    headers: { accept: "application/json" } 
  });
  return res.json();
}

export type AdminAnalyticsOverview = {
  success: boolean;
  total_claims: number;
  claims_analyzed: number;
  avg_risk_score: number;
  risk_distribution: Record<string, number>;
  decision_distribution: Record<string, number>;
  ai_rol_activity_by_capability: Record<string, number>;
  handler_action_distribution: Record<string, number>;
  claims_over_time: { date: string; count: number }[];
  business_rules_trigger_frequency: { rule_id: string; count: number }[];
  relationship_findings: { claims_with_findings: number; by_type: Record<string, number> };
  narrative_similarity: { claims_with_matches: number };
  active_assessments: number;
  pending_review: number;
  completed_assessments: number;
  overdue_assessments: number;
  claim_value_under_assessment: number;
};

export async function getAdminAnalyticsOverview(days = 30): Promise<AdminAnalyticsOverview> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/analytics/overview?days=${days}`, {
    headers: { accept: "application/json" },
  });
  return res.json();
}

export type LiveOperation = {
  claim_id: string;
  assignment_id: string;
  vehicle: string | null;
  assessor_name: string | null;
  status: string;
  ai_risk: string;
  fraud_risk_score: number | null;
  value: number | null;
  assigned_at: string | null;
};

export async function getLiveAssessmentOperations(params: {
  limit?: number; offset?: number; status?: string; risk_level?: string;
} = {}): Promise<{ success: boolean; total: number; limit: number; offset: number; operations: LiveOperation[] }> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") query.append(k, String(v)); });
  const res = await fetch(`${BASE_URL}/api/analysis/admin/live-operations?${query}`, { headers: { accept: "application/json" } });
  return res.json();
}

export type AiIntelligenceOverview = {
  success: boolean;
  images_analysed: number;
  damage_detections: number;
  assessor_confirmation_rate: number | null;
  human_override_rate: number | null;
  ai_confidence_avg: number | null;
  low_confidence_cases: number;
  pending_assessor_decisions: number;
  total_assessor_decisions_logged: number;
};

export async function getAiIntelligenceOverview(): Promise<AiIntelligenceOverview> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/ai/overview`, { headers: { accept: "application/json" } });
  return res.json();
}

export type AiComparisonData = {
  success: boolean;
  total_decisions: number;
  agreement_rate: number | null;
  disagreement_by_component: { component: string; total: number; disagreements: number; disagreement_rate: number }[];
  recent_decisions: { claim_id: string; component: string; ai_recommendation: string; assessor_decision: string; agreed: boolean; decided_at: string }[];
};

export async function getAiVsAssessorComparison(): Promise<AiComparisonData> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/ai/comparison`, { headers: { accept: "application/json" } });
  return res.json();
}

export type RiskQueueClaim = {
  claim_id: string;
  tier: "critical" | "review" | "normal";
  risk_score: number | null;
  created_at: string;
  estimated_cost: number;
  indicators: { type: string; severity: string; description: string; confidence: number }[];
};

export async function getAiRiskFraudQueue(limit = 30): Promise<{ success: boolean; total_flagged: number; critical_count: number; review_count: number; queue: RiskQueueClaim[] }> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/ai/risk-queue?limit=${limit}`, { headers: { accept: "application/json" } });
  return res.json();
}

export type DecisionTraceStep = { label: string; done: boolean; detail?: string };
export type DecisionTrace = {
  filename: string;
  source: "detector" | "whole_photo_scan";
  component: string;
  steps: DecisionTraceStep[];
  confidence: number | null;
  low_confidence: boolean;
  recommended_action: string;
  assessor_decision: string | null;
};

export async function getAiDecisionTrace(claimId: string): Promise<{ success: boolean; claim_id: string; traces: DecisionTrace[] }> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/ai/decision-trace/${claimId}`, { headers: { accept: "application/json" } });
  return res.json();
}

export type ClaimRecipient = { type: "member" | "assessor" | "analyst" | "repair_shop"; id: string; name: string; email: string | null };

export async function getClaimRecipients(claimId: string): Promise<{ success: boolean; recipients: ClaimRecipient[] }> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/claim/${claimId}/recipients`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function aiDraftClaimMessage(claimId: string, data: { recipientType: string; recipientName?: string; instruction?: string }): Promise<{ success: boolean; subject: string; body: string }> {
  const body = new URLSearchParams();
  body.append("recipient_type", data.recipientType);
  if (data.recipientName) body.append("recipient_name", data.recipientName);
  if (data.instruction) body.append("instruction", data.instruction);
  const res = await fetch(`${BASE_URL}/api/analysis/admin/claim/${claimId}/ai-draft-message`, {
    method: "POST", headers: { accept: "application/json" }, body,
  });
  return res.json();
}

export async function sendClaimMessage(claimId: string, data: { toEmail: string; subject: string; body: string; recipientType?: string }): Promise<{ success: boolean; detail?: string }> {
  const body = new URLSearchParams();
  body.append("to_email", data.toEmail);
  body.append("subject", data.subject);
  body.append("body", data.body);
  if (data.recipientType) body.append("recipient_type", data.recipientType);
  const res = await fetch(`${BASE_URL}/api/analysis/admin/claim/${claimId}/send-message`, {
    method: "POST", headers: { accept: "application/json" }, body,
  });
  return res.json();
}

export type RiskFactor = {
  key: string; label: string; icon: string;
  points: number; max_points: number; impact: "high" | "medium" | "low"; reasoning: string;
};
export type AiInvestigationSummary = {
  success: boolean;
  headline: string;
  risk_level: string;
  score: number;
  factors: RiskFactor[];
  what_to_review: string[];
  important_note: string;
};

export async function explainRiskScore(claimId: string, data: {
  riskBreakdown: any; riskLevel?: string; decision?: string;
  photoAnomalyCount?: number; narrativeIssueCount?: number; crossPartyIssueCount?: number;
  businessRuleFindings?: any[];
}): Promise<AiInvestigationSummary> {
  const body = new URLSearchParams();
  body.append("risk_breakdown", JSON.stringify(data.riskBreakdown));
  if (data.riskLevel) body.append("risk_level", data.riskLevel);
  if (data.decision) body.append("decision", data.decision);
  if (data.photoAnomalyCount) body.append("photo_anomaly_count", String(data.photoAnomalyCount));
  if (data.narrativeIssueCount) body.append("narrative_issue_count", String(data.narrativeIssueCount));
  if (data.crossPartyIssueCount) body.append("cross_party_issue_count", String(data.crossPartyIssueCount));
  if (data.businessRuleFindings) body.append("business_rule_findings", JSON.stringify(data.businessRuleFindings));
  const res = await fetch(`${BASE_URL}/api/analysis/admin/claim/${claimId}/explain-risk-score`, {
    method: "POST", headers: { accept: "application/json" }, body,
  });
  return res.json();
}

export type BusinessRuleConfigField = {
  key: string;
  label: string;
  group: string;
  default: number;
  unit: string;
  help: string;
  value: number;
  is_overridden: boolean;
};

export async function getBusinessRulesConfig(): Promise<{ success: boolean; fields: BusinessRuleConfigField[] }> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/business-rules-config`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function updateBusinessRulesConfig(updates: Record<string, number | null>, adminId: string): Promise<{ success: boolean; updated?: string[]; detail?: string }> {
  const body = new URLSearchParams();
  body.append("updates", JSON.stringify(updates));
  body.append("admin_id", adminId);
  const res = await fetch(`${BASE_URL}/api/analysis/admin/business-rules-config`, {
    method: "POST", headers: { accept: "application/json" }, body,
  });
  return res.json();
}

export type DamageDecisionRow = {
  filename: string; detection_index: number; component: string;
  ai_recommendation: string; assessor_decision: string; assessor_id: string; decided_at: string;
};

export async function getAdminClaimDetails(claimId: string): Promise<{
  success: boolean;
  claim_id: string;
  claim_details: any;
  member_info: any;
  policy_info: any;
  photos: ClaimPhoto[];
  documents: any[];
  assignments: any[];
  damage_decisions: DamageDecisionRow[];
}> {
  const res = await fetch(`${BASE_URL}/api/analysis/admin/claim/${claimId}/details`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function getClaimFullReport(claimId: string, includeTimeline = false) {
  const qs = includeTimeline ? "?include_timeline=true" : "";
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/full-report${qs}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function getClaimSummary(claimId: string) {
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/summary`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function getSimulationStatus(claimId: string) {
  const res = await fetch(`${BASE_URL}/api/analysis/claims/${claimId}/simulation-status`, { headers: { accept: "application/json" } });
  return res.json();
}

export type AiRolRecord = {
  record_id: string;
  claim_id: string;
  capability: string;
  recommendation: string;
  confidence: number | null;
  evidence: Record<string, unknown> | null;
  created_at: string;
  handler_action: "proceed" | "clarify" | "escalate" | "override" | null;
  handler_id: string | null;
  override_reason: string | null;
  decided_at: string | null;
};

export async function getAiRolAuditTrail(claimId: string): Promise<{ claim_id: string; records: AiRolRecord[] }> {
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/ai-rol`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function recordAiRolAction(data: {
  claim_id: string;
  handler_id: string;
  action: "proceed" | "clarify" | "escalate" | "override";
  capability?: string;
  reason?: string;
}) {
  const body = new URLSearchParams();
  body.append("handler_id", data.handler_id);
  body.append("action", data.action);
  if (data.capability) body.append("capability", data.capability);
  if (data.reason) body.append("reason", data.reason);
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${data.claim_id}/ai-rol/action`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}

export type ClaimDecision = {
  decision_id: string;
  claim_id: string;
  decision: "PAY" | "DENY" | "ESCALATE";
  reason: string | null;
  payout_amount: number | null;
  decided_by: string;
  decided_at: string;
};

export async function getClaimDecision(claimId: string): Promise<{
  claim_id: string;
  current_decision: ClaimDecision | null;
  history: ClaimDecision[];
}> {
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/decision`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function recordClaimDecision(data: {
  claim_id: string;
  decision: "PAY" | "DENY" | "ESCALATE";
  decided_by: string;
  reason?: string;
  payout_amount?: number;
}) {
  const body = new URLSearchParams();
  body.append("decision", data.decision);
  body.append("decided_by", data.decided_by);
  if (data.reason) body.append("reason", data.reason);
  if (data.payout_amount !== undefined) body.append("payout_amount", String(data.payout_amount));
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${data.claim_id}/decision`, {
    method: "POST",
    headers: { accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  return res.json();
}