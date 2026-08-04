export const BASE_URL = "http://127.0.0.1:8010";

export async function getMemberDetails(memberId: string) {
  const res = await fetch(`${BASE_URL}/api/member/${memberId}`, { headers: { accept: "application/json" } });
  return res.json();
}

export async function getMemberPolicies(memberId: string) {
  const res = await fetch(`${BASE_URL}/api/member/${memberId}/policies`, { headers: { accept: "application/json" } });
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
    "claim_type"
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

export async function assessNarrative(data: {
  narrative: string;
  claim_type?: string;
  round?: number;
}): Promise<{ sufficient: boolean; clarifying_question: string | null; missing_aspect: string | null }> {
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
  const res = await fetch(`${BASE_URL}/api/analysis/member`, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
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

export async function scheduleInspection(data: {
  assignment_id: string;
  inspection_date: string;
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
}) {
  const formData = new FormData();
  formData.append("claim_id", data.claim_id);
  formData.append("assessor_id", data.assessor_id);
  formData.append("damage_report", data.damage_report);
  formData.append("estimated_cost", String(data.estimated_cost));
  formData.append("inspection_date", data.inspection_date);
  data.photos.forEach((p) => formData.append("photos", p));
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