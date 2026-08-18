// Overridable via VITE_API_BASE_URL in a .env.local file so the devserver
// deployment doesn't need this source-level value hand-patched (and
// silently reverted) every time this file gets synced from a local dev
// checkout. .env.local is gitignored/never synced -- set it once on the
// devserver and it survives every future api.ts upload.
export const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8010";

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
  documentType: "police_abstract" | "id_document" | "garage_quote" | "other";
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

export async function notifyMemberToAddPhotos(claimId: string, requestedBy: string): Promise<{
  success: boolean;
  sent_to?: string;
  reason?: string;
}> {
  const body = new URLSearchParams();
  body.append("requested_by", requestedBy);
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/notify-member`, {
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

export async function getClaimFullReport(claimId: string) {
  const res = await fetch(`${BASE_URL}/api/analysis/claim/${claimId}/full-report`, { headers: { accept: "application/json" } });
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