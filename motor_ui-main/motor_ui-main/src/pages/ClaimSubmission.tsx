import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import MemberLayout from "@/layouts/MemberLayout";
import { submitMemberClaim } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export default function ClaimSubmission() {
  const navigate = useNavigate();
  const [claimData, setClaimData] = useState<any>(null);
  const [narrative, setNarrative] = useState("");
  const [estimatedCost, setEstimatedCost] = useState("250000");
  const [photos, setPhotos] = useState<File[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [successData, setSuccessData] = useState<any>(null); // State for API success response

  useEffect(() => {
    const cached = localStorage.getItem("claimResult");
    if (!cached) { 
      navigate("/member/coverage-check"); 
      return; 
    }
    setClaimData(JSON.parse(cached));
  }, [navigate]);

  if (!claimData && !successData) return null;

  const handleSubmit = async () => {
    setLoading(true);
    setError("");
    const memberId = localStorage.getItem("memberId") || "";
    const formData = JSON.parse(localStorage.getItem("coverageFormData") || "{}");
    
    try {
      const result = await submitMemberClaim({
        claim_id: claimData.claim_id,
        member_id: memberId,
        narrative,
        estimated_cost: Number(estimatedCost),
        location: formData.incident_location || "",
        incident_date: formData.incident_date || "",
        photos,
      });

      if (result.success) {
        // Clear workflow cache
        localStorage.removeItem("coverageResult");
        localStorage.removeItem("coverageFormData");
        localStorage.removeItem("claimResult");
        // Keep member session but refresh data if needed
        localStorage.removeItem("memberData");
        
        setSuccessData(result);
      } else {
        setError(result.message || "Submission failed. Please try again.");
      }
    } catch {
      setError("Server error during submission. Please check your connection.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <MemberLayout>
      <div className="p-8 max-w-[900px] mx-auto space-y-8 pb-20">
        
        {/* Success View */}
        {successData ? (
          <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="text-center space-y-4">
              <div className="size-20 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center mx-auto mb-4 shadow-sm border-4 border-white">
                <span className="material-symbols-outlined text-5xl">verified</span>
              </div>
              <h2 className="text-4xl font-black text-foreground tracking-tighter">{successData.message}</h2>
              <div className="flex items-center justify-center gap-3">
                <Badge variant="outline" className="font-mono text-sm px-4 py-1">REF: {successData.reference_number}</Badge>
                <Badge className="bg-primary text-white uppercase text-[10px] tracking-[0.2em] px-3">
                  {successData.status}
                </Badge>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Submission Details */}
              <Card className="border-none shadow-sm overflow-hidden">
                <CardHeader className="bg-muted/50 border-b">
                  <CardTitle className="text-xs font-black uppercase tracking-[0.2em] text-muted-foreground flex items-center gap-2">
                    <span className="material-symbols-outlined text-sm">summarize</span>
                    Submission Summary
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6 space-y-4">
                  <div className="flex justify-between items-center py-2 border-b border-dashed">
                    <span className="text-sm font-medium text-muted-foreground">Estimated Cost</span>
                    <span className="text-sm font-black text-primary">{successData.submission_details?.estimated_cost}</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-dashed">
                    <span className="text-sm font-medium text-muted-foreground">Incident Date</span>
                    <span className="text-sm font-bold text-foreground">{successData.submission_details?.incident_date}</span>
                  </div>
                  <div className="flex justify-between items-center py-2 border-b border-dashed">
                    <span className="text-sm font-medium text-muted-foreground">Location</span>
                    <span className="text-sm font-bold text-foreground">{successData.submission_details?.location}</span>
                  </div>
                  <div className="flex justify-between items-center py-2">
                    <span className="text-sm font-medium text-muted-foreground">Evidence Uploaded</span>
                    <span className="text-sm font-bold text-foreground">{successData.submission_details?.photos_uploaded} Files</span>
                  </div>
                </CardContent>
              </Card>

              {/* Next Steps */}
              <Card className="border-none shadow-sm overflow-hidden">
                <CardHeader className="bg-primary/5 border-b border-primary/10">
                  <CardTitle className="text-xs font-black uppercase tracking-[0.2em] text-primary flex items-center gap-2">
                    <span className="material-symbols-outlined text-sm">timeline</span>
                    What Happens Next
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-6 space-y-4">
                  {successData.next_steps?.map((step: string, i: number) => (
                    <div key={i} className="flex gap-4 items-start text-sm">
                      <div className="size-6 bg-primary text-white rounded-full flex items-center justify-center text-[10px] font-black shrink-0 mt-0.5 shadow-sm">
                        {i + 1}
                      </div>
                      <p className="font-semibold text-foreground/80 leading-relaxed">{step}</p>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>

            <div className="p-5 bg-card rounded-2xl border border-border flex justify-between items-center text-[11px] text-muted-foreground italic shadow-sm">
                <span>Submitted at: {new Date(successData.submitted_at).toLocaleString()}</span>
                <span>System: Xenova Core (KE)</span>
            </div>

            <div className="pt-6">
              <Link to="/member/dashboard" className="w-full py-5 bg-primary text-primary-foreground rounded-2xl font-black text-xs uppercase tracking-[0.2em] text-center block hover:brightness-110 transition-all shadow-xl shadow-primary/20">
                Return to Dashboard
              </Link>
            </div>
          </div>
        ) : (
          /* Form View */
          <>
            <div className="space-y-4">
              <div className="flex justify-between items-end">
                <div className="space-y-1">
                  <h1 className="text-4xl font-black text-foreground tracking-tighter">Final Submission</h1>
                  <p className="text-sm text-muted-foreground font-medium">Step 3 of 3 · Claim ID: <span className="font-mono font-bold text-primary">{claimData.claim_id}</span></p>
                </div>
                <div className="text-right hidden sm:block">
                  <p className="text-[10px] font-black uppercase text-primary tracking-widest">Progress</p>
                  <p className="text-xl font-black">100%</p>
                </div>
              </div>
              <div className="h-2 w-full bg-primary/10 rounded-full overflow-hidden">
                <div className="h-full bg-primary animate-pulse" style={{ width: "100%" }}></div>
              </div>
            </div>

            <div className="bg-card p-8 md:p-10 rounded-2xl border border-primary/10 shadow-xl shadow-primary/5 space-y-10">
              {/* Narrative */}
              <div className="space-y-3">
                <label className="text-[10px] font-black text-muted-foreground uppercase tracking-[0.2em]">Accident Narrative</label>
                <textarea 
                  className="w-full min-h-[220px] rounded-xl border border-border bg-background p-5 text-sm font-medium leading-relaxed focus:border-primary focus:ring-primary transition-all placeholder:italic" 
                  placeholder="Describe exactly what happened, including road conditions and vehicle behavior..." 
                  value={narrative} 
                  onChange={(e) => setNarrative(e.target.value)} 
                />
              </div>

              {/* Financial & Severity */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="space-y-3">
                  <label className="text-[10px] font-black text-muted-foreground uppercase tracking-[0.2em]">Estimated Repair Cost (KES)</label>
                  <div className="relative">
                    <span className="absolute left-4 top-1/2 -translate-y-1/2 text-primary font-black text-sm">KES</span>
                    <input 
                      className="w-full h-14 pl-14 pr-4 rounded-xl border border-border bg-background text-lg font-black focus:border-primary focus:ring-primary transition-all" 
                      type="number" 
                      value={estimatedCost} 
                      onChange={(e) => setEstimatedCost(e.target.value)} 
                    />
                  </div>
                </div>
                <div className="space-y-3">
                  <label className="text-[10px] font-black text-muted-foreground uppercase tracking-[0.2em]">Apparent Severity</label>
                  <div className="h-14 flex items-center">
                    <Badge className="bg-amber-100 text-amber-700 hover:bg-amber-100 border border-amber-200 px-4 py-1.5 font-bold rounded-lg">
                      MODERATE DAMAGE
                    </Badge>
                  </div>
                </div>
              </div>

              {/* Photo Upload */}
              <div className="space-y-4">
                <div className="flex justify-between items-end">
                  <label className="text-[10px] font-black text-muted-foreground uppercase tracking-[0.2em]">Photo Evidence</label>
                  <span className="text-[10px] font-bold text-primary">{photos.length} Files Attached</span>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                  {photos.map((photo, i) => (
                    <div key={i} className="group relative aspect-square rounded-2xl overflow-hidden border border-border shadow-sm">
                      <img src={URL.createObjectURL(photo)} alt={photo.name} className="w-full h-full object-cover transition-transform group-hover:scale-105" />
                      <button onClick={() => setPhotos(photos.filter((_, idx) => idx !== i))} className="absolute top-2 right-2 size-8 bg-destructive text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-lg">
                        <span className="material-symbols-outlined text-sm">close</span>
                      </button>
                    </div>
                  ))}
                  <label className="aspect-square flex flex-col items-center justify-center rounded-2xl border-2 border-dashed border-primary/20 bg-primary/5 hover:bg-primary/10 transition-all text-primary gap-2 cursor-pointer group">
                    <span className="material-symbols-outlined text-4xl group-hover:scale-110 transition-transform">add_a_photo</span>
                    <span className="text-[10px] font-black uppercase tracking-widest">Attach Media</span>
                    <input type="file" className="hidden" multiple accept="image/*" onChange={(e) => { if (e.target.files) setPhotos([...photos, ...Array.from(e.target.files)]); }} />
                  </label>
                </div>
              </div>

              {error && (
                <Alert variant="destructive" className="border-2">
                  <span className="material-symbols-outlined">error</span>
                  <AlertTitle className="font-black uppercase text-xs tracking-widest">Submission Protocol Error</AlertTitle>
                  <AlertDescription className="text-xs font-bold">{error}</AlertDescription>
                </Alert>
              )}

              {/* Actions */}
              <div className="flex flex-col-reverse sm:flex-row items-center justify-between pt-8 border-t gap-6">
                <button onClick={() => navigate(-1)} className="flex items-center gap-2 text-muted-foreground hover:text-foreground font-black text-xs uppercase tracking-widest transition-colors">
                  <span className="material-symbols-outlined">arrow_back</span>
                  Previous Step
                </button>
                <div className="flex gap-4 w-full sm:w-auto">
                  <button className="flex-1 sm:flex-none px-10 h-14 rounded-xl border-2 border-primary/20 text-primary font-black text-xs uppercase tracking-widest hover:bg-primary/5 transition-all">Save Draft</button>
                  <button 
                    onClick={handleSubmit} 
                    disabled={loading || !narrative} 
                    className="flex-1 sm:min-w-[200px] px-10 h-14 rounded-xl bg-primary text-primary-foreground font-black text-xs uppercase tracking-[0.2em] shadow-xl shadow-primary/20 hover:brightness-110 transition-all disabled:opacity-50"
                  >
                    {loading ? "Processing..." : "Submit Claim"}
                  </button>
                </div>
              </div>
            </div>

            <div className="flex gap-4 p-6 bg-blue-50/50 rounded-2xl border border-blue-100">
              <span className="material-symbols-outlined text-blue-500">gpp_maybe</span>
              <p className="text-xs text-blue-800 leading-relaxed font-medium italic">
                By submitting this claim, you attest that the information provided is a truthful account of the incident. AI-driven forensic analysis will be used to verify consistency across reports and media evidence.
              </p>
            </div>
          </>
        )}
      </div>
    </MemberLayout>
  );
}