import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import MemberLayout from "@/layouts/MemberLayout";
import { createClaim, getMemberPolicies } from "@/lib/api";
import { Badge } from "@/components/ui/badge";

export default function ClaimCreation() {
  const navigate = useNavigate();
  const [coverage, setCoverage] = useState<any>(null);
  const [activePolicy, setActivePolicy] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [fetchingPolicies, setFetchingPolicies] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const memberId = localStorage.getItem("memberId");
    const cachedCoverage = localStorage.getItem("coverageResult");

    if (!memberId || !cachedCoverage) {
      navigate("/member/coverage-check");
      return;
    }

    const coverageData = JSON.parse(cachedCoverage);
    setCoverage(coverageData);

    getMemberPolicies(memberId)
      .then((res) => {
        if (res.success && res.data.length > 0) {
          const match = res.data.find(
            (p: any) => p.policy_number === coverageData.policy_number || p.policy_id === coverageData.policy_number
          );
          setActivePolicy(match || res.data[0]);
        }
        setFetchingPolicies(false);
      })
      .catch(() => {
        setFetchingPolicies(false);
        setError("Could not retrieve official policy details.");
      });
  }, [navigate]);

  const handleCreateClaim = async () => {
    if (!activePolicy) {
      setError("No valid policy selected.");
      return;
    }

    setLoading(true);
    setError("");
    const formData = JSON.parse(localStorage.getItem("coverageFormData") || "{}");
    const memberId = localStorage.getItem("memberId") || "";

    try {
      const result = await createClaim({
        coverage_check_id: coverage.check_id,
        member_id: memberId,
        policy_id: activePolicy.policy_id,
        incident_date: formData.incident_date,
        incident_location: formData.incident_location,
        brief_description: formData.brief_description,
        claim_type: formData.claim_type,
      });

      if (result.success) {
        localStorage.setItem("claimResult", JSON.stringify(result));
        navigate("/member/claim-submission");
      } else {
        setError(result.message || "Failed to create claim record.");
      }
    } catch {
      setError("Server error during claim creation.");
    } finally {
      setLoading(false);
    }
  };

  // Helper to determine banner styles based on decision
  const getDecisionStyles = () => {
    const decision = coverage?.coverage_decision?.toLowerCase() || "";
    if (decision.includes("confirmed") || decision.includes("eligible") || decision.includes("verified") || decision.includes("yes")) {
      return {
        bg: "bg-emerald-50 border-emerald-100",
        iconBg: "bg-emerald-500",
        icon: "gpp_good",
        text: "text-emerald-900",
        subtext: "text-emerald-700/80"
      };
    } else if (decision.includes("denied") || decision.includes("ineligible") || decision.includes("no")) {
      return {
        bg: "bg-destructive/10 border-destructive/20",
        iconBg: "bg-destructive",
        icon: "gpp_bad",
        text: "text-destructive",
        subtext: "text-destructive/80"
      };
    } else {
      return {
        bg: "bg-amber-50 border-amber-100",
        iconBg: "bg-amber-500",
        icon: "info",
        text: "text-amber-900",
        subtext: "text-amber-700/80"
      };
    }
  };

  const banner = getDecisionStyles();

  if (fetchingPolicies) return (
    <MemberLayout>
      <div className="flex items-center justify-center h-full">
        <p className="text-muted-foreground animate-pulse font-bold">Syncing policy data...</p>
      </div>
    </MemberLayout>
  );

  return (
    <MemberLayout>
      <div className="p-8 max-w-[960px] mx-auto space-y-8">
        <div className="space-y-3">
          <div className="flex justify-between items-center text-sm font-bold">
            <span className="text-muted-foreground uppercase tracking-widest">Step 2 of 3</span>
            <span className="text-primary uppercase tracking-widest">Verification Complete</span>
          </div>
          <div className="h-2 w-full bg-primary/10 rounded-full overflow-hidden">
            <div className="h-full bg-primary transition-all duration-1000" style={{ width: "66%" }}></div>
          </div>
        </div>

        <div className="text-center space-y-2">
          <h1 className="text-4xl font-black text-foreground tracking-tighter">Initialize Claim</h1>
          <p className="text-muted-foreground">Confirming coverage eligibility for your selected policy.</p>
        </div>

        {activePolicy && (
          <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-sm">
            <div className="bg-primary p-4 text-primary-foreground flex justify-between items-center">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined">verified</span>
                <span className="text-xs font-black uppercase tracking-widest">Official Policy Record</span>
              </div>
              <Badge variant="secondary" className="font-mono text-[10px]">{activePolicy.policy_id}</Badge>
            </div>
            <div className="p-6 grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="space-y-1">
                <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Policy Number</p>
                <p className="font-bold text-lg">{activePolicy.policy_number}</p>
              </div>
              <div className="space-y-1">
                <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Cover Type</p>
                <p className="font-bold text-lg">{activePolicy.cover_type}</p>
              </div>
              <div className="space-y-1">
                <p className="text-[10px] font-black text-muted-foreground uppercase tracking-widest">Status</p>
                <div className="flex items-center gap-2 text-emerald-600 font-bold">
                  <span className="size-2 rounded-full bg-emerald-500 animate-pulse"></span>
                  {activePolicy.status}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Dynamic Coverage Decision Banner */}
        <div className={`${banner.bg} border rounded-2xl p-6 flex flex-col md:flex-row gap-6 items-center transition-colors duration-500`}>
          <div className={`size-16 ${banner.iconBg} text-white rounded-full flex items-center justify-center shrink-0 shadow-lg`}>
            <span className="material-symbols-outlined text-4xl">{banner.icon}</span>
          </div>
          <div className="flex-1 space-y-1">
            <h3 className={`text-xl font-black tracking-tight ${banner.text}`}>
              Coverage Decision: {coverage.coverage_decision}
            </h3>
            <p className={`text-sm leading-relaxed ${banner.subtext}`}>
              {coverage.coverage_decision.toLowerCase().includes('denied') 
                ? "Unfortunately, based on our system rules, this incident may not be covered under the current policy terms."
                : "Based on your policy limits and the incident description provided, your claim is eligible for processing."}
              <br />
              <strong className="mt-1 block">Applicable Excess: KES {Number(coverage.applicable_excess).toLocaleString()}</strong>
            </p>
          </div>
        </div>

        {/* Verification Highlights Section */}
        <div className="space-y-4">
          <h4 className="text-sm font-black uppercase tracking-widest text-muted-foreground">
            Verification Highlights
          </h4>
          
          {/* Changed from grid-cols-2 to grid-cols-1 to allow full width for long error messages */}
          <div className="grid grid-cols-1 gap-4">
            {coverage.reasons?.map((reason: string, i: number) => {
              const isNegative = coverage.coverage_decision?.toLowerCase().includes('not') || 
                                coverage.coverage_decision?.toLowerCase().includes('denied');
              
              return (
                <div 
                  key={i} 
                  className={`flex gap-4 p-5 bg-card border ${isNegative ? 'border-destructive/20' : 'border-border'} rounded-xl shadow-sm h-auto`}
                >
                  <span className={`material-symbols-outlined ${isNegative ? 'text-destructive' : 'text-primary'} text-2xl shrink-0`}>
                    {isNegative ? 'cancel' : 'check_circle'}
                  </span>
                  <div className="flex flex-col gap-1 overflow-hidden">
                    <p className={`text-sm font-bold uppercase tracking-tight ${isNegative ? 'text-destructive' : 'text-primary'}`}>
                      {isNegative ? 'System Alert' : 'Verification Point'}
                    </p>
                    {/* Added break-words and whitespace-pre-wrap to handle the long API/Gemini error strings */}
                    <p className="text-sm italic text-muted-foreground leading-relaxed break-words whitespace-pre-wrap">
                      {reason}
                    </p>
                  </div>
                </div>
              );
            })}

            {/* Fallback if reasons is empty but decision is negative */}
            {(!coverage.reasons || coverage.reasons.length === 0) && (
              <div className="p-5 bg-card border border-border rounded-xl text-sm italic text-muted-foreground">
                No specific reasons provided by the assessment engine.
              </div>
            )}
          </div>
        </div>

        {error && (
          <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-xl text-destructive text-sm font-bold flex items-center gap-2">
            <span className="material-symbols-outlined">error</span>
            {error}
          </div>
        )}

        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-8 border-t">
          <button 
            onClick={() => navigate(-1)} 
            className="w-full sm:w-auto px-10 py-4 text-sm font-black uppercase tracking-widest text-muted-foreground hover:text-foreground transition-colors"
          >
            Go Back
          </button>
          <button 
            onClick={handleCreateClaim} 
            disabled={loading || !activePolicy || coverage.coverage_decision.toLowerCase().includes('denied')} 
            className="w-full sm:max-w-md px-10 py-4 bg-primary text-primary-foreground rounded-xl font-black uppercase tracking-widest shadow-xl shadow-primary/20 hover:brightness-110 transition-all flex items-center justify-center gap-3 disabled:opacity-50"
          >
            {loading ? "Registering Claim..." : "Create Claim Record"}
            {!loading && <span className="material-symbols-outlined">arrow_forward</span>}
          </button>
        </div>
      </div>
    </MemberLayout>
  );
}