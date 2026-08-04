import { useState } from "react";
import { useNavigate } from "react-router-dom";
import MemberLayout from "@/layouts/MemberLayout";
import { checkCoverage } from "@/lib/api";

export default function CoverageCheck() {
  const navigate = useNavigate();
  const memberId = localStorage.getItem("memberId") || "";
  const memberName = localStorage.getItem("memberName") || "";

  const [formData, setFormData] = useState({
    claim_type: "motor",
    incident_date: new Date().toISOString().split("T")[0],
    driver_name: memberName,
    incident_location: "",
    brief_description: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const result = await checkCoverage({ member_id: memberId, ...formData });
      if (result.success) {
        localStorage.setItem("coverageResult", JSON.stringify(result));
        localStorage.setItem("coverageFormData", JSON.stringify(formData));
        navigate("/member/claim-creation");
      } else {
        setError("Coverage check failed. Please try again.");
      }
    } catch {
      setError("Could not connect to server.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <MemberLayout>
      <div className="p-8 max-w-4xl mx-auto">
        {/* Progress */}
        <div className="flex flex-col gap-3 mb-8">
          <div className="flex gap-6 justify-between items-center">
            <p className="text-foreground text-base font-semibold">Step 1 of 3: Coverage Check</p>
            <p className="text-primary text-sm font-bold">33% Complete</p>
          </div>
          <div className="rounded-full bg-primary/10 h-2 w-full overflow-hidden">
            <div className="h-full rounded-full bg-primary" style={{ width: "33%" }}></div>
          </div>
        </div>

        <h1 className="text-3xl font-black text-foreground tracking-tight mb-2">Check Your Coverage</h1>
        <p className="text-muted-foreground mb-8">Verify your policy covers the incident before filing a claim.</p>

        <div className="bg-card rounded-xl p-8 border border-primary/10 shadow-sm">
          <form className="space-y-6" onSubmit={handleSubmit}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="flex flex-col gap-2">
                <label className="text-foreground text-sm font-bold">Claim Type</label>
                <select className="rounded-lg border border-border bg-background h-12 px-4 text-sm font-medium text-foreground focus:border-primary focus:ring-primary" value={formData.claim_type} onChange={(e) => setFormData({ ...formData, claim_type: e.target.value })}>
                  <option value="motor">Motor</option>
                  <option value="domestic">Domestic</option>
                  <option value="marine">Marine</option>
                </select>
              </div>
              <div className="flex flex-col gap-2">
                <label className="text-foreground text-sm font-bold">Incident Date</label>
                <input className="rounded-lg border border-border bg-background h-12 px-4 text-sm font-medium text-foreground focus:border-primary focus:ring-primary" type="date" value={formData.incident_date} onChange={(e) => setFormData({ ...formData, incident_date: e.target.value })} />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-foreground text-sm font-bold">Driver Name</label>
              <input className="rounded-lg border border-border bg-muted h-12 px-4 text-sm font-medium text-muted-foreground cursor-not-allowed" readOnly value={formData.driver_name} />
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-foreground text-sm font-bold">Incident Location</label>
              <div className="relative flex items-center">
                <span className="material-symbols-outlined absolute left-3 text-muted-foreground">location_on</span>
                <input className="w-full rounded-lg border border-border bg-background h-12 pl-10 pr-4 text-sm font-medium text-foreground focus:border-primary focus:ring-primary" type="text" placeholder="e.g. Thika Road, Nairobi" value={formData.incident_location} onChange={(e) => setFormData({ ...formData, incident_location: e.target.value })} required />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <label className="text-foreground text-sm font-bold">Brief Description of Incident</label>
              <textarea className="w-full rounded-lg border border-border bg-background p-4 text-sm font-medium text-foreground focus:border-primary focus:ring-primary placeholder:text-muted-foreground" rows={4} placeholder="e.g. Rear-ended at traffic lights" value={formData.brief_description} onChange={(e) => setFormData({ ...formData, brief_description: e.target.value })} required />
            </div>
            {error && <p className="text-destructive text-sm">{error}</p>}
            <div className="flex flex-col sm:flex-row gap-4 pt-4">
              <button className="flex-1 bg-primary hover:bg-primary/90 text-primary-foreground font-bold h-14 rounded-lg flex items-center justify-center gap-2 transition-all shadow-lg shadow-primary/20 disabled:opacity-50" type="submit" disabled={loading}>
                <span className="material-symbols-outlined">shield</span>
                {loading ? "Checking..." : "Check Coverage"}
              </button>
              <button className="sm:w-32 bg-muted text-foreground font-bold h-14 rounded-lg hover:bg-muted/80 transition-all" type="button" onClick={() => navigate(-1)}>Cancel</button>
            </div>
          </form>
        </div>

        <div className="mt-8 flex gap-4 p-4 rounded-lg bg-primary/5 border border-primary/10">
          <span className="material-symbols-outlined text-primary">info</span>
          <div>
            <h4 className="text-foreground text-sm font-bold">What happens next?</h4>
            <p className="text-muted-foreground text-sm">Our AI will instantly verify your policy limits and excess amounts before asking for photos of the damage in Step 2.</p>
          </div>
        </div>
      </div>
    </MemberLayout>
  );
}
