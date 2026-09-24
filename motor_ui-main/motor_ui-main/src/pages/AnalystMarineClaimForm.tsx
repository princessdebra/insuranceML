import { ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import AnalystLayout from "@/layouts/AnalystLayout";
import {
  searchMembers, MemberSearchResult,
  getMemberPolicies,
  checkCoverage,
  createClaim,
  submitMemberClaim,
  uploadDocument,
  uploadTrackingData,
} from "@/lib/api";

/**
 * Analyst intake for Marine Hull, Marine Cargo, and Goods in Transit claims
 * -- the "OM analyst claim form" channel the demo runbook's M01-M03 cases
 * use. Deliberately a separate page from AnalystClaimFormUpload.tsx: that
 * flow is built entirely around photographing a paper "Motor Accident Claim
 * Form" and OCR-extracting vehicle/driver fields, which has no equivalent
 * for a vessel, a shipment, or a road consignment -- forcing Marine through
 * that flow would mean bending a Motor-shaped form around data it was never
 * designed to hold. This page instead does direct structured entry (the
 * analyst already has the loss details from a call/email, per the demo
 * script), matching how the M01-M03 scripts describe the flow: search the
 * policy, enter loss details, upload supporting documents, submit.
 */

type MarineClaimType = "marine_hull" | "marine_cargo" | "goods_in_transit";

const CLAIM_TYPE_META: Record<MarineClaimType, { label: string; icon: string; lossTypes: string[] }> = {
  marine_hull: {
    label: "Marine Hull",
    icon: "sailing",
    lossTypes: ["Collision", "Grounding or stranding", "Machinery damage", "Fire", "Storm damage", "Heavy weather and contact damage"],
  },
  marine_cargo: {
    label: "Marine Cargo",
    icon: "inventory_2",
    lossTypes: ["Water damage", "Shortage or non-delivery", "Breakage", "Temperature damage", "Theft", "General average"],
  },
  goods_in_transit: {
    label: "Goods in Transit",
    icon: "local_shipping",
    lossTypes: ["Collision or overturning", "Theft or hijacking", "Shortage", "Wet damage", "Loading or unloading damage", "Temperature damage"],
  },
};

const DOCUMENT_TYPES: { value: "invoice" | "packing_list" | "bill_of_lading" | "delivery_note" | "survey_report" | "master_statement" | "police_abstract" | "other"; label: string }[] = [
  { value: "invoice", label: "Invoice" },
  { value: "packing_list", label: "Packing List" },
  { value: "bill_of_lading", label: "Bill of Lading" },
  { value: "delivery_note", label: "Delivery / Gate / Weighbridge Record" },
  { value: "survey_report", label: "Survey Report" },
  { value: "master_statement", label: "Master's Statement" },
  { value: "police_abstract", label: "Police Abstract" },
  { value: "other", label: "Other" },
];

type StagedDoc = { file: File; documentType: typeof DOCUMENT_TYPES[number]["value"] };

export default function AnalystMarineClaimForm() {
  const analystId = localStorage.getItem("analystId") || "";

  const [claimType, setClaimType] = useState<MarineClaimType>("marine_cargo");

  // Member/policy matching -- same convention as AnalystClaimFormUpload.tsx:
  // only an existing member with an existing policy can have a claim filed.
  const [memberQuery, setMemberQuery] = useState("");
  const [memberResults, setMemberResults] = useState<MemberSearchResult[]>([]);
  const [hasSearchedMembers, setHasSearchedMembers] = useState(false);
  const [searchingMembers, setSearchingMembers] = useState(false);
  const [selectedMember, setSelectedMember] = useState<MemberSearchResult | null>(null);
  const [policies, setPolicies] = useState<any[]>([]);
  const [selectedPolicyId, setSelectedPolicyId] = useState("");

  // Loss details
  const [incidentDate, setIncidentDate] = useState("");
  const [incidentLocation, setIncidentLocation] = useState("");
  const [lossType, setLossType] = useState("");
  const [narrative, setNarrative] = useState("");
  const [estimatedValue, setEstimatedValue] = useState("");

  // Type-specific fields -- these flow into structured_details, which the
  // business rules engine reads directly (BR-SUB-003, BR-LOC-004,
  // BR-SEC-006, BR-GIT-011 all key off these). Not narrative text: a
  // narrative mention doesn't reliably parse back into an exact vessel ID
  // or vehicle registration, so these are their own fields.
  const [vesselId, setVesselId] = useState("");
  const [navigationZone, setNavigationZone] = useState("");
  const [shipmentId, setShipmentId] = useState("");
  const [vehicleReg, setVehicleReg] = useState("");
  const [driverName, setDriverName] = useState("");
  const [transporterName, setTransporterName] = useState("");

  const [stagedDocs, setStagedDocs] = useState<StagedDoc[]>([]);
  const [pendingDocType, setPendingDocType] = useState<typeof DOCUMENT_TYPES[number]["value"]>("invoice");

  // AIS/GPS (Hull) or telematics (GIT) evidence -- Cargo temperature logs
  // reuse the same "telematics" upload slot with a different data_type
  // label, since all three are just "a CSV/log file", not images.
  const [stagedTracking, setStagedTracking] = useState<File | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [createdClaimId, setCreatedClaimId] = useState("");

  const runMemberSearch = async (q: string) => {
    if (!q.trim()) return;
    setSearchingMembers(true);
    setHasSearchedMembers(true);
    try {
      const res = await searchMembers(q.trim());
      setMemberResults(res.results || []);
    } catch {
      setMemberResults([]);
    } finally {
      setSearchingMembers(false);
    }
  };

  const selectMember = async (m: MemberSearchResult) => {
    setSelectedMember(m);
    setPolicies([]);
    setSelectedPolicyId("");
    try {
      const res = await getMemberPolicies(m.member_id);
      const list = res.data || [];
      setPolicies(list);
      // Prefer a policy matching the selected claim type, since a member
      // can hold several policies (e.g. Motor and Marine Cargo both).
      const match = list.find((p: any) => p.policy_type === claimType);
      setSelectedPolicyId((match || list[0])?.policy_id || "");
    } catch {
      setPolicies([]);
    }
  };

  const addStagedDoc = (file: File) => {
    setStagedDocs((prev) => [...prev, { file, documentType: pendingDocType }]);
  };

  const buildStructuredDetails = (): Record<string, string> => {
    const details: Record<string, string> = {};
    if (incidentDate) details.incident_datetime = incidentDate;
    if (claimType === "marine_hull") {
      if (vesselId) details.vessel_id = vesselId;
      if (navigationZone) details.navigation_zone = navigationZone;
    } else if (claimType === "marine_cargo") {
      if (shipmentId) details.shipment_id = shipmentId;
    } else if (claimType === "goods_in_transit") {
      if (vehicleReg) details.vehicle_reg = vehicleReg;
      if (driverName) details.driver_name = driverName;
      if (transporterName) details.transporter_name = transporterName;
    }
    return details;
  };

  const handleSubmit = async () => {
    setSubmitError("");
    if (!selectedMember) {
      setSubmitError("Search for and select the matching member first.");
      return;
    }
    if (!selectedPolicyId) {
      setSubmitError("Select a policy for this member.");
      return;
    }
    if (!incidentDate || !incidentLocation.trim() || !lossType) {
      setSubmitError("Incident date, location, and loss type are required.");
      return;
    }
    if (!narrative.trim()) {
      setSubmitError("Enter a narrative describing what happened.");
      return;
    }

    setSubmitting(true);
    try {
      const briefDescription = `${lossType}: ${narrative.slice(0, 180)}`;

      const coverage = await checkCoverage({
        member_id: selectedMember.member_id,
        claim_type: claimType,
        incident_date: incidentDate.slice(0, 10),
        incident_location: incidentLocation,
        brief_description: briefDescription,
      });
      if (coverage.detail && !coverage.success) {
        setSubmitError(`Coverage check failed: ${coverage.detail}`);
        return;
      }
      if (!coverage.success || coverage.coverage_decision !== "COVERED") {
        setSubmitError(coverage.message || (coverage.reasons?.join(" ")) || "This policy is not covered for this claim.");
        return;
      }

      const claimRes = await createClaim({
        coverage_check_id: coverage.check_id,
        member_id: selectedMember.member_id,
        policy_id: selectedPolicyId,
        incident_date: incidentDate.slice(0, 10),
        incident_location: incidentLocation,
        brief_description: briefDescription,
        claim_type: claimType,
        filed_by_analyst_id: analystId,
        filing_method: "form",
      });
      if (!claimRes.success && !claimRes.claim_id) {
        setSubmitError(claimRes.detail || "Couldn't create the claim.");
        return;
      }
      const claimId = claimRes.claim_id;

      const submitRes = await submitMemberClaim({
        claim_id: claimId,
        member_id: selectedMember.member_id,
        narrative: `${lossType}\n\n${narrative}`,
        estimated_cost: Number(estimatedValue) || 0,
        location: incidentLocation,
        incident_date: incidentDate.slice(0, 10),
        photos: [],
        structured_details: buildStructuredDetails(),
      });
      if (!submitRes.success) {
        setSubmitError(submitRes.detail || "Claim was created but details submission failed -- check the claim manually.");
        return;
      }

      // Upload each staged document -- best-effort per file, matching the
      // paper-form flow's convention (one failed attachment shouldn't
      // undo an already-created, already-submitted claim).
      for (const doc of stagedDocs) {
        try {
          await uploadDocument({
            claimId, party: "member", documentType: doc.documentType,
            uploaderId: selectedMember.member_id, file: doc.file,
          });
        } catch {
          // non-fatal, continue with the rest
        }
      }

      if (stagedTracking) {
        try {
          await uploadTrackingData({
            claimId, party: "member",
            dataType: claimType === "marine_hull" ? "ais_gps" : claimType === "goods_in_transit" ? "telematics" : "temperature_log",
            uploaderId: selectedMember.member_id, file: stagedTracking,
          });
        } catch {
          // non-fatal
        }
      }

      setCreatedClaimId(claimId);
    } catch {
      setSubmitError("Something went wrong creating this claim -- please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setSelectedMember(null);
    setPolicies([]);
    setSelectedPolicyId("");
    setMemberQuery("");
    setMemberResults([]);
    setHasSearchedMembers(false);
    setIncidentDate("");
    setIncidentLocation("");
    setLossType("");
    setNarrative("");
    setEstimatedValue("");
    setVesselId("");
    setNavigationZone("");
    setShipmentId("");
    setVehicleReg("");
    setDriverName("");
    setTransporterName("");
    setStagedDocs([]);
    setStagedTracking(null);
    setCreatedClaimId("");
    setSubmitError("");
  };

  if (createdClaimId) {
    return (
      <AnalystLayout>
        <div className="p-8 max-w-2xl mx-auto w-full">
          <div className="bg-card rounded-xl border border-border shadow-sm p-8 text-center space-y-4">
            <span className="material-symbols-outlined text-emerald-600 text-5xl">check_circle</span>
            <div>
              <h3 className="text-xl font-bold text-foreground">Claim Created</h3>
              <p className="text-sm text-muted-foreground mt-1">
                Claim <b className="text-foreground">{createdClaimId}</b> ({CLAIM_TYPE_META[claimType].label}) has been filed and is now processing.
              </p>
            </div>
            <div className="flex items-center justify-center gap-3">
              <Link to={`/analyst/claim/${createdClaimId}`} className="px-4 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors">
                View Claim Report
              </Link>
              <button type="button" onClick={resetForm} className="px-4 py-2.5 border border-border rounded-lg text-sm font-bold hover:bg-muted transition-colors">
                File Another Claim
              </button>
            </div>
          </div>
        </div>
      </AnalystLayout>
    );
  }

  return (
    <AnalystLayout>
      <div className="p-8 max-w-4xl mx-auto w-full space-y-6">
        <div>
          <h2 className="text-3xl font-bold text-foreground flex items-center gap-2">
            <span className="material-symbols-outlined text-primary text-3xl">anchor</span>
            File a Marine Claim
          </h2>
          <p className="text-muted-foreground mt-1">Marine Hull, Marine Cargo, or Goods in Transit -- enter the loss details as reported.</p>
        </div>

        {/* Claim type */}
        <div className="bg-card rounded-xl border border-border shadow-sm p-5">
          <p className="text-xs font-black uppercase tracking-wider text-muted-foreground mb-3">Claim Type</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {(Object.keys(CLAIM_TYPE_META) as MarineClaimType[]).map((ct) => (
              <button
                key={ct}
                type="button"
                onClick={() => { setClaimType(ct); setLossType(""); setSelectedPolicyId(""); }}
                className={`flex items-center gap-2.5 px-4 py-3 rounded-lg border text-left transition-colors ${
                  claimType === ct ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
                }`}
              >
                <span className={`material-symbols-outlined text-[22px] ${claimType === ct ? "text-primary" : "text-muted-foreground"}`}>{CLAIM_TYPE_META[ct].icon}</span>
                <span className={`text-sm font-bold ${claimType === ct ? "text-primary" : "text-foreground"}`}>{CLAIM_TYPE_META[ct].label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Member matching */}
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="px-5 py-3.5 border-b border-border">
            <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Policy Search</p>
          </div>
          <div className="p-5 space-y-4">
            <div className="flex items-center gap-2">
              <input
                value={memberQuery}
                onChange={(e) => setMemberQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && runMemberSearch(memberQuery)}
                placeholder="Search by company/policyholder name, phone, or member ID"
                className="flex-1 text-sm px-3 py-2.5 border border-border rounded-lg bg-background outline-none focus:ring-2 focus:ring-primary/40"
              />
              <button type="button" onClick={() => runMemberSearch(memberQuery)} disabled={searchingMembers} className="px-4 py-2.5 bg-muted rounded-lg text-sm font-bold hover:bg-muted/70 transition-colors">
                {searchingMembers ? "..." : "Search"}
              </button>
            </div>
            {hasSearchedMembers && !searchingMembers && memberResults.length === 0 && (
              <div className="text-xs text-amber-700 bg-amber-500/5 border border-amber-500/20 rounded-lg p-3">
                No matching member found for "{memberQuery}". A claim can only be filed for a member with an existing policy on file.
              </div>
            )}
            {memberResults.length > 0 && (
              <div className="space-y-1.5">
                {memberResults.map((m) => (
                  <button
                    type="button"
                    key={m.member_id}
                    onClick={() => selectMember(m)}
                    className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors ${selectedMember?.member_id === m.member_id ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"}`}
                  >
                    <p className="text-sm font-bold text-foreground">{m.name}</p>
                    <p className="text-xs text-muted-foreground">{m.member_id} · {m.phone} · {m.email}</p>
                  </button>
                ))}
              </div>
            )}
            {selectedMember && policies.length > 0 && (
              <div>
                <p className="text-[10px] font-bold uppercase text-muted-foreground mb-1.5">Policy</p>
                <select
                  value={selectedPolicyId}
                  onChange={(e) => setSelectedPolicyId(e.target.value)}
                  className="w-full text-sm px-3 py-2.5 border border-border rounded-lg bg-background outline-none focus:ring-2 focus:ring-primary/40"
                >
                  {policies.map((p: any) => (
                    <option key={p.policy_id} value={p.policy_id}>
                      {p.policy_number} · {p.cover_type}{p.policy_type && p.policy_type !== claimType ? ` (${p.policy_type} -- check this is the right policy)` : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {selectedMember && policies.length === 0 && (
              <p className="text-xs text-amber-700">No policies on file for this member -- cannot proceed until one exists.</p>
            )}
          </div>
        </div>

        {/* Loss details */}
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="px-5 py-3.5 border-b border-border">
            <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Loss Details</p>
          </div>
          <div className="p-5 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Incident Date/Time"><input type="datetime-local" value={incidentDate} onChange={(e) => setIncidentDate(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
              <Field label="Location"><input value={incidentLocation} onChange={(e) => setIncidentLocation(e.target.value)} placeholder="e.g. Mombasa port to Nairobi warehouse" className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
              <Field label="Loss Type">
                <select value={lossType} onChange={(e) => setLossType(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none">
                  <option value="">Select...</option>
                  {CLAIM_TYPE_META[claimType].lossTypes.map((lt) => <option key={lt} value={lt}>{lt}</option>)}
                </select>
              </Field>
              <Field label="Estimated Value (KES)"><input type="number" value={estimatedValue} onChange={(e) => setEstimatedValue(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
            </div>

            {/* Type-specific fields */}
            {claimType === "marine_hull" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                <Field label="Vessel ID"><input value={vesselId} onChange={(e) => setVesselId(e.target.value)} placeholder="e.g. VES-M02" className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
                <Field label="Navigation Zone (as reported)"><input value={navigationZone} onChange={(e) => setNavigationZone(e.target.value)} placeholder="e.g. Kenyan coastal waters" className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
              </div>
            )}
            {claimType === "marine_cargo" && (
              <div className="grid grid-cols-1 gap-3 pt-1">
                <Field label="Shipment ID / Declaration Reference"><input value={shipmentId} onChange={(e) => setShipmentId(e.target.value)} placeholder="e.g. SHP-M01" className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
              </div>
            )}
            {claimType === "goods_in_transit" && (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
                <Field label="Vehicle Registration"><input value={vehicleReg} onChange={(e) => setVehicleReg(e.target.value)} placeholder="e.g. KDG 730T" className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
                <Field label="Driver"><input value={driverName} onChange={(e) => setDriverName(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
                <Field label="Transporter"><input value={transporterName} onChange={(e) => setTransporterName(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
              </div>
            )}

            <Field label="Narrative"><textarea value={narrative} onChange={(e) => setNarrative(e.target.value)} rows={5} placeholder="Describe what was reported -- who, what, when, how discovered." className="w-full text-sm px-3 py-2.5 border border-border rounded-lg bg-background outline-none resize-y" /></Field>
          </div>
        </div>

        {/* Evidence */}
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="px-5 py-3.5 border-b border-border">
            <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Supporting Documents</p>
          </div>
          <div className="p-5 space-y-3">
            <div className="flex flex-wrap gap-2">
              {DOCUMENT_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => setPendingDocType(t.value)}
                  className={`text-[10px] font-bold uppercase px-2.5 py-1.5 rounded-lg border transition-colors ${
                    pendingDocType === t.value ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:border-primary/40"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <input
              type="file"
              accept="image/*,application/pdf"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) addStagedDoc(f); e.target.value = ""; }}
              className="text-sm"
            />
            {stagedDocs.length > 0 && (
              <div className="space-y-1.5">
                {stagedDocs.map((d, i) => (
                  <div key={i} className="flex items-center justify-between text-xs bg-muted/30 rounded-lg px-3 py-2">
                    <span className="text-foreground font-medium">{d.file.name}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-muted-foreground uppercase text-[10px] font-bold">{DOCUMENT_TYPES.find((t) => t.value === d.documentType)?.label}</span>
                      <button type="button" onClick={() => setStagedDocs((prev) => prev.filter((_, pi) => pi !== i))} className="text-destructive">
                        <span className="material-symbols-outlined text-[16px]">close</span>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {(claimType === "marine_hull" || claimType === "goods_in_transit" || claimType === "marine_cargo") && (
          <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
            <div className="px-5 py-3.5 border-b border-border">
              <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">
                {claimType === "marine_hull" ? "AIS / GPS Track" : claimType === "goods_in_transit" ? "Telematics Log" : "Temperature Log"}
              </p>
            </div>
            <div className="p-5 space-y-2">
              <p className="text-xs text-muted-foreground">
                {claimType === "marine_hull"
                  ? "CSV export of the vessel's AIS/GPS track around the incident time, if available."
                  : claimType === "goods_in_transit"
                    ? "CSV export of the vehicle's telematics (speed, position, lateral acceleration) around the incident time, if available."
                    : "CSV export of the reefer/temperature-logger readings for the transit, if available."}
              </p>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setStagedTracking(e.target.files?.[0] || null)}
                className="text-sm"
              />
              {stagedTracking && <p className="text-[11px] text-emerald-600">{stagedTracking.name} selected</p>}
            </div>
          </div>
        )}

        {!selectedMember && (
          <p className="text-xs text-amber-700 px-1 flex items-center gap-1.5"><span className="material-symbols-outlined text-[14px]">info</span>Search for and select the matching member above to enable claim creation.</p>
        )}
        {selectedMember && !selectedPolicyId && (
          <p className="text-xs text-amber-700 px-1 flex items-center gap-1.5"><span className="material-symbols-outlined text-[14px]">info</span>Select a policy for {selectedMember.name} above to enable claim creation.</p>
        )}
        {submitError && <p className="text-sm text-destructive font-semibold px-1">{submitError}</p>}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            <span className="material-symbols-outlined text-[18px]">{submitting ? "hourglass_top" : "check_circle"}</span>
            {submitting ? "Creating claim..." : "Validate & Create Claim"}
          </button>
        </div>
      </div>
    </AnalystLayout>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="text-[10px] font-bold uppercase text-muted-foreground mb-1">{label}</p>
      {children}
    </div>
  );
}
