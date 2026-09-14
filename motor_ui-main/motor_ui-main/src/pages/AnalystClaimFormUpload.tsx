import { ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import AnalystLayout from "@/layouts/AnalystLayout";
import {
  extractClaimForm, ClaimFormFields,
  searchMembers, MemberSearchResult,
  getMemberPolicies,
  checkCoverage,
  createClaim,
  submitMemberClaim,
  uploadDocument,
  notifyMemberToAddPhotos,
  MEMBER_FIELD_LABELS,
} from "@/lib/api";

/**
 * The real analyst workflow: a member fills in and hands over (or the
 * analyst receives) the paper Old Mutual "Motor Accident Claim Form" --
 * this replaces re-interviewing them over the phone (ClaimChatbot's
 * analystMode) for that case. Upload the page photos, review what the
 * vision model read off the form, match it to the member+policy on file,
 * then run the same coverage-check -> create-claim -> submit pipeline
 * every other intake path uses.
 */
type Step = "upload" | "review" | "done";

function joinList(items: { [k: string]: string | undefined }[], template: (i: any) => string): string {
  const lines = items.map(template).filter(Boolean);
  return lines.join("\n");
}

export default function AnalystClaimFormUpload() {
  const [step, setStep] = useState<Step>("upload");
  const [files, setFiles] = useState<File[]>([]);
  const [extracting, setExtracting] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<ClaimFormFields | null>(null);
  const [confidence, setConfidence] = useState(0);
  const [qualityNotes, setQualityNotes] = useState("");
  const [appearsGenuine, setAppearsGenuine] = useState<boolean | null>(null);

  // Editable, derived-from-extraction claim payload
  const [incidentDate, setIncidentDate] = useState("");
  const [incidentLocation, setIncidentLocation] = useState("");
  const [narrative, setNarrative] = useState("");
  const [estimatedCost, setEstimatedCost] = useState("");
  const [driverName, setDriverName] = useState("");
  const [thirdPartyInvolved, setThirdPartyInvolved] = useState<"Yes" | "No">("No");
  const [thirdPartyDetails, setThirdPartyDetails] = useState("");
  const [policeReported, setPoliceReported] = useState<"Yes" | "No">("No");
  const [policeObNumber, setPoliceObNumber] = useState("");
  const [witnessesPresent, setWitnessesPresent] = useState<"Yes" | "No">("No");
  const [witnessDetails, setWitnessDetails] = useState("");
  const [injuriesReported, setInjuriesReported] = useState<"Yes" | "No">("No");
  const [injuryDetails, setInjuryDetails] = useState("");

  // Member/policy matching -- this flow only matches EXISTING members and
  // policies. It deliberately does not offer to create one on the fly:
  // that path was tried and removed -- a claims analyst creating member/
  // policy records isn't a real workflow, only an existing member with an
  // existing policy can have a claim filed against it.
  const [memberQuery, setMemberQuery] = useState("");
  const [memberResults, setMemberResults] = useState<MemberSearchResult[]>([]);
  const [hasSearchedMembers, setHasSearchedMembers] = useState(false);
  const [searchingMembers, setSearchingMembers] = useState(false);
  const [selectedMember, setSelectedMember] = useState<MemberSearchResult | null>(null);
  const [policies, setPolicies] = useState<any[]>([]);
  const [selectedPolicyId, setSelectedPolicyId] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [createdClaimId, setCreatedClaimId] = useState("");
  const [photoRequestSent, setPhotoRequestSent] = useState(false);
  const [photoRequestReason, setPhotoRequestReason] = useState("");
  const [missingFieldsSent, setMissingFieldsSent] = useState<string[]>([]);

  const analystId = localStorage.getItem("analystId") || "";

  const handleExtract = async () => {
    if (files.length === 0) return;
    setExtracting(true);
    setError("");
    try {
      const res = await extractClaimForm(files, analystId);
      if (!res.success) {
        setError(res.detail || "Couldn't read this form -- try clearer, well-lit photos of each page.");
        return;
      }
      const f = res.parsed_fields;
      setFields(f);
      setConfidence(res.extraction_confidence);
      setQualityNotes(res.quality_notes);
      setAppearsGenuine(res.document_appears_genuine);

      setIncidentDate(f.accident_date || "");
      setIncidentLocation(f.accident_place || "");
      setDriverName(f.driver_name || f.insured_full_name || "");
      const narrativeParts = [
        f.damage_description && `Reported damage: ${f.damage_description}`,
        f.driver_statement && `Driver's statement: ${f.driver_statement}`,
        f.owner_statement && `Owner's statement: ${f.owner_statement}`,
      ].filter(Boolean);
      setNarrative(narrativeParts.join("\n\n") || "");

      const hasThirdParty = (f.third_party_vehicles?.length || 0) > 0 || (f.third_party_property_damaged?.length || 0) > 0;
      setThirdPartyInvolved(hasThirdParty ? "Yes" : "No");
      setThirdPartyDetails(
        joinList(f.third_party_vehicles || [], (v) => `Vehicle ${v.reg_no || "(reg. unknown)"}, owner ${v.owner_name || "unknown"}${v.insurer ? `, insured with ${v.insurer}` : ""}.`)
        + (f.third_party_property_damaged?.length ? "\n" + joinList(f.third_party_property_damaged, (p) => `Property damaged: ${p.property_damaged || "unspecified"}, owner ${p.owner_name || "unknown"}.`) : "")
      );

      setPoliceReported(f.police_involved ? "Yes" : "No");
      setPoliceObNumber("");

      const hasWitnesses = (f.witnesses?.length || 0) > 0;
      setWitnessesPresent(hasWitnesses ? "Yes" : "No");
      setWitnessDetails(joinList(f.witnesses || [], (w) => `${w.name || "Unnamed witness"}${w.address ? `, ${w.address}` : ""}.`));

      const hasInjuries = (f.persons_injured?.length || 0) > 0;
      setInjuriesReported(hasInjuries ? "Yes" : "No");
      setInjuryDetails(joinList(f.persons_injured || [], (p) => `${p.name || "Unnamed person"}${p.relationship_to_insured ? ` (${p.relationship_to_insured})` : ""}: ${p.apparent_injuries || "injuries not specified"}.`));

      setMemberQuery(f.insured_full_name || "");
      setStep("review");
      if (f.insured_full_name) {
        runMemberSearch(f.insured_full_name);
      }
    } catch {
      setError("Extraction failed -- check the connection and try again.");
    } finally {
      setExtracting(false);
    }
  };

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
      // Auto-match by policy number extracted from the form, if we got one.
      const match = fields?.policy_no ? list.find((p: any) => p.policy_number === fields.policy_no) : null;
      setSelectedPolicyId((match || list[0])?.policy_id || "");
    } catch {
      setPolicies([]);
    }
  };

  const handleConfirm = async () => {
    if (!fields) return;
    if (!selectedMember) {
      setSubmitError("Select the matching member above before confirming -- search for them by name, phone, or member ID and click their result.");
      return;
    }
    if (!selectedPolicyId) {
      setSubmitError("Select a policy for this member above before confirming.");
      return;
    }
    if (!incidentDate) {
      setSubmitError("Incident date is required -- the form didn't have a readable date, or it needs correcting above.");
      return;
    }
    setSubmitting(true);
    setSubmitError("");
    try {
      const coverage = await checkCoverage({
        member_id: selectedMember.member_id,
        claim_type: "motor",
        incident_date: incidentDate,
        incident_location: incidentLocation,
        driver_name: driverName,
        brief_description: fields.damage_description || narrative.slice(0, 200) || "Motor accident claim filed from a physical claim form.",
      });
      if (coverage.detail && !coverage.success) {
        // A raw {"detail": "..."} shape means the backend errored (e.g. a
        // 500) rather than returning an actual coverage decision -- don't
        // relabel a system error as a business "not covered" rejection,
        // they need different follow-up (retry vs. genuinely ineligible).
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
        incident_date: incidentDate,
        incident_location: incidentLocation,
        brief_description: fields.damage_description || narrative.slice(0, 200),
        claim_type: "motor",
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
        narrative: narrative || fields.damage_description || "Claim filed from physical claim form.",
        estimated_cost: Number(estimatedCost) || 0,
        location: incidentLocation,
        incident_date: incidentDate,
        photos: [],
        third_party_involved: thirdPartyInvolved,
        third_party_details: thirdPartyDetails,
        police_reported: policeReported,
        police_ob_number: policeObNumber,
        witnesses_present: witnessesPresent,
        witness_details: witnessDetails,
        injuries_reported: injuriesReported,
        injury_details: injuryDetails,
      });
      if (!submitRes.success) {
        setSubmitError(submitRes.detail || "Claim was created but details submission failed -- check the claim manually.");
        return;
      }

      // Attach the original form page photos to the now-real claim, for
      // the audit trail -- best-effort, doesn't block claim creation if it fails.
      try {
        for (const file of files) {
          await uploadDocument({ claimId, party: "member", documentType: "claim_form", uploaderId: selectedMember.member_id, file });
        }
      } catch {
        // non-fatal
      }

      // A paper claim form never carries photos -- unlike the phone-wizard
      // flow (which can at least ask the caller to send some), there's no
      // path to get damage photos into this claim except asking the member
      // directly. Best-effort: a claim already exists and is usable either
      // way, this is just how the member finds out to add their evidence.
      // Anything the paper form left blank -- the member fills these in
      // themselves from the same link, alongside the photos.
      const missingFields = Object.keys(MEMBER_FIELD_LABELS).filter((key) => {
        if (key === "estimated_cost") return !estimatedCost;
        if (key === "location") return !incidentLocation.trim();
        if (key === "narrative") return !narrative.trim();
        return false;
      });

      try {
        const notifyRes = await notifyMemberToAddPhotos(claimId, analystId, missingFields);
        setPhotoRequestSent(notifyRes.success);
        setPhotoRequestReason(notifyRes.success ? (notifyRes.sent_to || "") : (notifyRes.reason || ""));
        setMissingFieldsSent(notifyRes.success ? missingFields : []);
      } catch {
        setPhotoRequestSent(false);
        setPhotoRequestReason("Couldn't reach the notification service.");
      }

      setCreatedClaimId(claimId);
      setStep("done");
    } catch {
      setSubmitError("Something went wrong creating this claim -- please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AnalystLayout>
      <div className="p-8 max-w-4xl mx-auto w-full space-y-6">
        <div>
          <h2 className="text-3xl font-bold text-foreground flex items-center gap-2">
            <span className="material-symbols-outlined text-primary text-3xl">document_scanner</span>
            Upload Claim Form
          </h2>
          <p className="text-muted-foreground mt-1">Photograph or scan the member's filled-in paper claim form -- the system reads it for you.</p>
        </div>

        {step === "upload" && (
          <div className="bg-card rounded-xl border border-border shadow-sm p-6 space-y-4">
            <label className="block border-2 border-dashed border-border rounded-xl p-8 text-center cursor-pointer hover:border-primary/50 hover:bg-muted/30 transition-colors">
              <input
                type="file"
                accept="image/*,application/pdf"
                multiple
                className="hidden"
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
              />
              <span className="material-symbols-outlined text-4xl text-muted-foreground">add_photo_alternate</span>
              <p className="text-sm font-bold text-foreground mt-2">Click to select page photos or a PDF</p>
              <p className="text-xs text-muted-foreground mt-1">One photo per page (up to 8), or a single multi-page PDF -- Section A-D of the Motor Accident Claim Form</p>
            </label>

            {files.length > 0 && (
              <div className="flex gap-2 flex-wrap">
                {files.map((f, i) => (
                  <div key={i} className="relative">
                    {f.type === "application/pdf" ? (
                      <div className="size-20 rounded-lg border border-border bg-muted/40 flex flex-col items-center justify-center gap-1 px-1">
                        <span className="material-symbols-outlined text-2xl text-muted-foreground">picture_as_pdf</span>
                        <span className="text-[9px] text-muted-foreground text-center truncate w-full">{f.name}</span>
                      </div>
                    ) : (
                      <img src={URL.createObjectURL(f)} alt={f.name} className="size-20 object-cover rounded-lg border border-border" />
                    )}
                    <button
                      type="button"
                      onClick={() => setFiles(files.filter((_, fi) => fi !== i))}
                      className="absolute -top-1.5 -right-1.5 size-5 rounded-full bg-destructive text-white flex items-center justify-center text-[10px]"
                    >
                      <span className="material-symbols-outlined text-[12px]">close</span>
                    </button>
                  </div>
                ))}
              </div>
            )}

            {error && <p className="text-sm text-destructive font-semibold">{error}</p>}

            <button
              type="button"
              onClick={handleExtract}
              disabled={files.length === 0 || extracting}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              <span className="material-symbols-outlined text-[18px]">{extracting ? "hourglass_top" : "auto_awesome"}</span>
              {extracting ? "Reading the form..." : "Extract Claim Details"}
            </button>
          </div>
        )}

        {step === "review" && fields && (
          <div className="space-y-6">
            <div className={`rounded-xl border p-4 flex items-start gap-3 ${confidence >= 60 ? "bg-emerald-500/5 border-emerald-500/20" : "bg-amber-500/5 border-amber-500/20"}`}>
              <span className={`material-symbols-outlined ${confidence >= 60 ? "text-emerald-600" : "text-amber-600"}`}>{confidence >= 60 ? "check_circle" : "warning"}</span>
              <div>
                <p className="text-sm font-bold text-foreground">Extraction confidence: {confidence}%{appearsGenuine === false && " -- document authenticity uncertain"}</p>
                {qualityNotes && <p className="text-xs text-muted-foreground mt-0.5">{qualityNotes}</p>}
                <p className="text-xs text-muted-foreground mt-1">Everything below was read automatically -- check it against the physical form before confirming.</p>
              </div>
            </div>

            {/* Member matching */}
            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-border">
                <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Match to Member &amp; Policy</p>
              </div>
              <div className="p-5 space-y-4">
                <div className="flex items-center gap-2">
                  <input
                    value={memberQuery}
                    onChange={(e) => setMemberQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && runMemberSearch(memberQuery)}
                    placeholder="Search by name, phone, or member ID"
                    className="flex-1 text-sm px-3 py-2.5 border border-border rounded-lg bg-background outline-none focus:ring-2 focus:ring-primary/40"
                  />
                  <button type="button" onClick={() => runMemberSearch(memberQuery)} disabled={searchingMembers} className="px-4 py-2.5 bg-muted rounded-lg text-sm font-bold hover:bg-muted/70 transition-colors">
                    {searchingMembers ? "..." : "Search"}
                  </button>
                </div>
                {fields.insured_full_name && (
                  <p className="text-xs text-muted-foreground">From the form: <b className="text-foreground">{fields.insured_full_name}</b>{fields.insured_id_no ? ` · ID ${fields.insured_id_no}` : ""}{fields.insured_phone ? ` · ${fields.insured_phone}` : ""}</p>
                )}
                {hasSearchedMembers && !searchingMembers && memberResults.length === 0 && (
                  <div className="text-xs text-amber-700 bg-amber-500/5 border border-amber-500/20 rounded-lg p-3">
                    No matching member found for "{memberQuery}". Double-check the spelling -- a claim can only be filed for a member with an existing policy on file.
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
                        <option key={p.policy_id} value={p.policy_id}>{p.policy_number} · {p.cover_type}</option>
                      ))}
                    </select>
                  </div>
                )}
                {selectedMember && policies.length === 0 && (
                  <p className="text-xs text-amber-700">No policies on file for this member -- cannot proceed until one exists.</p>
                )}
              </div>
            </div>

            {/* Incident details */}
            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-border">
                <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Incident Details</p>
              </div>
              <div className="p-5 space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Incident Date"><input type="date" value={incidentDate} onChange={(e) => setIncidentDate(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
                  <Field label="Location"><input value={incidentLocation} onChange={(e) => setIncidentLocation(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
                  <Field label="Driver"><input value={driverName} onChange={(e) => setDriverName(e.target.value)} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
                  <Field label="Vehicle"><input disabled value={[fields.vehicle_year, fields.vehicle_make, fields.vehicle_model, fields.vehicle_reg_no].filter(Boolean).join(" ") || "Not read from form"} className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-muted/40 text-muted-foreground outline-none" /></Field>
                  <Field label="Estimated Repair Cost (KES)"><input type="number" value={estimatedCost} onChange={(e) => setEstimatedCost(e.target.value)} placeholder="Not on the form -- enter if known" className="w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" /></Field>
                </div>
                <Field label="Narrative"><textarea value={narrative} onChange={(e) => setNarrative(e.target.value)} rows={5} className="w-full text-sm px-3 py-2.5 border border-border rounded-lg bg-background outline-none resize-y" /></Field>
              </div>
            </div>

            {/* Third party / police / witnesses / injuries */}
            <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-border">
                <p className="text-xs font-black uppercase tracking-wider text-muted-foreground">Other Parties &amp; Reporting</p>
              </div>
              <div className="p-5 space-y-4">
                <YesNoField label="Third party involved" value={thirdPartyInvolved} onChange={setThirdPartyInvolved} details={thirdPartyDetails} onDetailsChange={setThirdPartyDetails} />
                <div>
                  <YesNoField label="Reported to police" value={policeReported} onChange={setPoliceReported} />
                  {policeReported === "Yes" && (
                    <input value={policeObNumber} onChange={(e) => setPoliceObNumber(e.target.value)} placeholder="OB number (if known)" className="mt-2 w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none" />
                  )}
                  {(fields.police_station || fields.police_constable_number) && (
                    <p className="text-xs text-muted-foreground mt-1">From the form: {fields.police_station && `Station: ${fields.police_station}. `}{fields.police_constable_number && `Constable No: ${fields.police_constable_number}.`}</p>
                  )}
                </div>
                <YesNoField label="Witnesses present" value={witnessesPresent} onChange={setWitnessesPresent} details={witnessDetails} onDetailsChange={setWitnessDetails} />
                <YesNoField label="Injuries reported" value={injuriesReported} onChange={setInjuriesReported} details={injuryDetails} onDetailsChange={setInjuryDetails} />
              </div>
            </div>

            {!selectedMember && (
              <p className="text-xs text-amber-700 px-1 flex items-center gap-1.5"><span className="material-symbols-outlined text-[14px]">info</span>Search for and select the matching member above to enable claim creation.</p>
            )}
            {selectedMember && !selectedPolicyId && (
              <p className="text-xs text-amber-700 px-1 flex items-center gap-1.5"><span className="material-symbols-outlined text-[14px]">info</span>Select a policy for {selectedMember.name} above to enable claim creation.</p>
            )}
            {submitError && <p className="text-sm text-destructive font-semibold px-1">{submitError}</p>}

            <div className="flex items-center gap-3">
              <button type="button" onClick={() => setStep("upload")} className="px-4 py-2.5 border border-border rounded-lg text-sm font-bold hover:bg-muted transition-colors">Back</button>
              <button
                type="button"
                onClick={handleConfirm}
                disabled={submitting}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                <span className="material-symbols-outlined text-[18px]">{submitting ? "hourglass_top" : "check_circle"}</span>
                {submitting ? "Creating claim..." : "Confirm & Create Claim"}
              </button>
            </div>
          </div>
        )}

        {step === "done" && (
          <div className="bg-card rounded-xl border border-border shadow-sm p-8 text-center space-y-4">
            <span className="material-symbols-outlined text-emerald-600 text-5xl">check_circle</span>
            <div>
              <h3 className="text-xl font-bold text-foreground">Claim Created</h3>
              <p className="text-sm text-muted-foreground mt-1">Claim <b className="text-foreground">{createdClaimId}</b> has been filed from the uploaded form and is now processing.</p>
            </div>

            <div className={`text-left text-xs rounded-lg p-3.5 flex items-start gap-2.5 ${photoRequestSent ? "bg-emerald-500/5 border border-emerald-500/20 text-emerald-700" : "bg-amber-500/5 border border-amber-500/20 text-amber-700"}`}>
              <span className="material-symbols-outlined text-[16px] mt-0.5">{photoRequestSent ? "mark_email_read" : "warning"}</span>
              <div>
                {photoRequestSent ? (
                  <p>
                    A paper claim form has no photos attached -- emailed {photoRequestReason || "the member"} a link to add damage photos to this claim
                    {missingFieldsSent.length > 0 && (
                      <> and fill in {missingFieldsSent.map((k) => MEMBER_FIELD_LABELS[k]).join(", ")}, which the form left blank.</>
                    )}
                    {missingFieldsSent.length === 0 && "."}
                  </p>
                ) : (
                  <p>A paper claim form has no photos attached, and the request to email the member asking for photos didn't go through ({photoRequestReason || "unknown reason"}). Follow up with them directly so the claim doesn't stall on missing evidence.</p>
                )}
              </div>
            </div>

            <div className="flex items-center justify-center gap-3">
              <Link to="/analyst/dashboard" className="px-4 py-2.5 border border-border rounded-lg text-sm font-bold hover:bg-muted transition-colors">Back to Dashboard</Link>
              <button
                type="button"
                onClick={() => { setStep("upload"); setFiles([]); setFields(null); setSelectedMember(null); setPolicies([]); setCreatedClaimId(""); setPhotoRequestSent(false); setPhotoRequestReason(""); setMissingFieldsSent([]); }}
                className="px-4 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors"
              >
                Upload Another Form
              </button>
            </div>
          </div>
        )}
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

function YesNoField({
  label, value, onChange, details, onDetailsChange,
}: {
  label: string; value: "Yes" | "No"; onChange: (v: "Yes" | "No") => void;
  details?: string; onDetailsChange?: (v: string) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-foreground">{label}</p>
        <div className="flex gap-1.5">
          {(["Yes", "No"] as const).map((opt) => (
            <button
              type="button"
              key={opt}
              onClick={() => onChange(opt)}
              className={`px-3 py-1 rounded-lg text-xs font-bold transition-colors ${value === opt ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/70"}`}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>
      {value === "Yes" && onDetailsChange && (
        <textarea
          value={details}
          onChange={(e) => onDetailsChange(e.target.value)}
          rows={2}
          placeholder="Details"
          className="mt-2 w-full text-sm px-3 py-2 border border-border rounded-lg bg-background outline-none resize-y"
        />
      )}
    </div>
  );
}
