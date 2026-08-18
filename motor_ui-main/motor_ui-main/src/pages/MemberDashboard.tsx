import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import MemberLayout from "@/layouts/MemberLayout";
import { getMemberDetails } from "@/lib/api";

export default function MemberDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const memberId = localStorage.getItem("memberId");
    if (!memberId) {
      navigate("/member/login");
      return;
    }
    const cached = localStorage.getItem("memberData");
    if (cached) {
      setData(JSON.parse(cached));
      setLoading(false);
      return;
    }
    getMemberDetails(memberId)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [navigate]);

  if (loading) {
    return (
      <MemberLayout>
        <div className="flex items-center justify-center h-full">
          <p className="text-muted-foreground">Loading...</p>
        </div>
      </MemberLayout>
    );
  }

  if (!data?.success) {
    return (
      <MemberLayout>
        <div className="flex items-center justify-center h-full">
          <p className="text-destructive">Failed to load data</p>
        </div>
      </MemberLayout>
    );
  }

  const member = data.data;
  const policies = data.policies || [];
  const claims = data.claims || [];
  const statistics = data.statistics || {
    total_policies: 0,
    active_policies: 0,
    total_claims: 0,
    high_risk_claims: 0,
    pending_claims: 0,
  };

  const getRiskBadgeStyles = (level: string) => {
    switch (level?.toLowerCase()) {
      case "pending":
        return "bg-amber-100 text-amber-800 border-amber-200";
      case "low":
        return "bg-emerald-100 text-emerald-800 border-emerald-200";
      case "medium":
        return "bg-orange-100 text-orange-800 border-orange-200";
      case "high":
        return "bg-destructive/10 text-destructive border-destructive/20";
      default:
        return "bg-muted text-muted-foreground border-border";
    }
  };

  return (
    <MemberLayout memberName={member.name} memberId={member.member_id}>
      <div className="p-8 space-y-8">
        {/* Welcome */}
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 bg-card p-8 rounded-xl border border-primary/10 shadow-sm">
          <div className="space-y-2">
            <h2 className="text-3xl font-black tracking-tight text-foreground">
              Welcome back, {member.name.split(" ")[0]}
            </h2>
            <p className="text-muted-foreground max-w-md">
              Your insurance portfolio is up to date. You have{" "}
              {statistics.active_policies} active{" "}
              {statistics.active_policies === 1 ? "policy" : "policies"} and{" "}
              {statistics.total_claims} registered{" "}
              {statistics.total_claims === 1 ? "claim" : "claims"}.
            </p>
          </div>
          <Link
            to="/member/coverage-check"
            className="flex items-center gap-2 bg-primary text-primary-foreground px-6 py-3 rounded-lg font-bold hover:bg-primary/90 transition-all shadow-lg shadow-primary/20"
          >
            <span className="material-symbols-outlined">add_circle</span>
            File a Claim
          </Link>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          {[
            {
              icon: "description",
              label: "Total Policies",
              value: statistics.total_policies,
              color: "text-primary",
              bg: "bg-primary/10",
            },
            {
              icon: "shield_with_heart",
              label: "Active Policies",
              value: statistics.active_policies,
              color: "text-emerald-600",
              bg: "bg-emerald-100",
            },
            {
              icon: "assignment",
              label: "Total Claims",
              value: statistics.total_claims,
              color: "text-amber-600",
              bg: "bg-amber-100",
            },
            {
              icon: "pending_actions",
              label: "Pending Claims",
              value: statistics.pending_claims !== undefined ? statistics.pending_claims : 0,
              color: "text-blue-600",
              bg: "bg-blue-100",
            },
          ].map((stat) => (
            <div
              key={stat.label}
              className="bg-card p-6 rounded-xl border border-primary/10 shadow-sm"
            >
              <div className="flex items-center justify-between mb-4">
                <span className={`material-symbols-outlined ${stat.color} ${stat.bg} p-2 rounded-lg`}>
                  {stat.icon}
                </span>
              </div>
              <p className="text-sm font-medium text-muted-foreground">{stat.label}</p>
              <p className="text-3xl font-bold mt-1 text-foreground">
                {String(stat.value).padStart(2, "0")}
              </p>
            </div>
          ))}
        </div>

        {/* Recent Policy */}
        {policies.length > 0 && (
          <div className="bg-card p-6 rounded-xl border border-primary/10 shadow-sm">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-lg font-bold text-foreground">Active Policies</h3>
              <Link to="/member/policies" className="text-primary text-sm font-bold hover:underline">
                View All →
              </Link>
            </div>
            {policies.map((policy: any) => (
              <div
                key={policy.policy_id}
                className="flex flex-col md:flex-row md:items-center justify-between p-4 bg-background rounded-lg border border-primary/5"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-primary uppercase">Active</span>
                    <span className="size-1.5 rounded-full bg-primary"></span>
                    <span className="text-xs font-medium text-muted-foreground">
                      {policy.policy_type} Insurance
                    </span>
                  </div>
                  <h4 className="text-lg font-bold text-foreground">
                    Policy: {policy.policy_number}
                  </h4>
                  <p className="text-sm text-muted-foreground">
                    {policy.cover_type} · KES {Number(policy.sum_insured).toLocaleString()}
                  </p>
                </div>
                <div className="flex items-center gap-3 mt-4 md:mt-0">
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-100 text-emerald-700 text-xs font-bold">
                    <span className="size-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    {policy.policy_status || policy.status}
                  </span>
                  <Link
                    to="/member/policies"
                    className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-all"
                  >
                    View Details
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Recent Claims */}
        {claims.length > 0 && (
          <div className="bg-card p-6 rounded-xl border border-primary/10 shadow-sm">
            <h3 className="text-lg font-bold mb-4 text-foreground">Claims History</h3>
            <div className="space-y-3">
              {claims.map((claim: any) => (
                <div
                  key={claim.claim_id}
                  className="flex flex-col lg:flex-row lg:items-center justify-between p-5 bg-background rounded-xl border border-primary/5 gap-4"
                >
                  <div className="flex-1 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-black text-primary">{claim.claim_id}</span>
                      <span className="text-xs text-muted-foreground">
                        • Policy: {claim.policy_id}
                      </span>
                      {claim.created_at && (
                        <span className="text-xs text-muted-foreground">
                          • Created: {claim.created_at.split(" ")[0]}
                        </span>
                      )}
                    </div>

                    <p className="text-sm font-medium text-foreground line-clamp-2">
                      {claim.narrative || (
                        <span className="text-muted-foreground italic">
                          No narrative provided yet (Initial eligibility verified)
                        </span>
                      )}
                    </p>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground pt-1">
                      {claim.location && (
                        <span className="flex items-center gap-1">
                          <span className="material-symbols-outlined text-sm">location_on</span>
                          {claim.location}
                        </span>
                      )}
                      {claim.estimated_cost !== undefined && (
                        <span className="flex items-center gap-1 font-semibold text-foreground">
                          <span className="material-symbols-outlined text-sm text-primary">payments</span>
                          KES {Number(claim.estimated_cost).toLocaleString()}
                        </span>
                      )}
                      {claim.fraud_risk_score !== undefined && claim.fraud_risk_score > 0 && (
                        <span className="flex items-center gap-1">
                          <span className="material-symbols-outlined text-sm">psychology</span>
                          AI Score: {claim.fraud_risk_score}%
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center justify-between lg:justify-end gap-3 border-t lg:border-t-0 pt-3 lg:pt-0">
                    <span
                      className={`px-3 py-1.5 rounded-full text-xs font-bold border capitalize ${getRiskBadgeStyles(
                        claim.risk_level
                      )}`}
                    >
                      {claim.risk_level} Risk
                    </span>
                    <Link
                      to={`/member/claim/${claim.claim_id}`}
                      className="text-xs font-bold text-primary hover:underline px-3 py-1.5 hover:bg-primary/5 rounded-lg transition-colors"
                    >
                      View Details
                    </Link>
                    <Link
                      to="/member/claim-submission"
                      onClick={() => {
                        localStorage.setItem(
                          "claimResult",
                          JSON.stringify({ claim_id: claim.claim_id })
                        );
                      }}
                      className="text-xs font-bold text-primary hover:underline px-3 py-1.5 hover:bg-primary/5 rounded-lg transition-colors"
                    >
                      Manage
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Quick Actions */}
        <div>
          <h3 className="text-lg font-bold mb-6 text-foreground">Explore Services</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { icon: "request_quote", title: "Get a Quote", desc: "Instant motor & life quotes" },
              { icon: "support_agent", title: "Talk to Us", desc: "24/7 Support via WhatsApp" },
              {
                icon: "location_searching",
                title: "Find a Branch",
                desc: "Locate Xenova Core Kenya offices",
              },
              { icon: "download", title: "Tax Certificates", desc: "Download for your returns" },
            ].map((item) => (
              <button
                key={item.title}
                className="bg-card p-4 rounded-xl border border-primary/5 hover:border-primary/40 hover:shadow-md transition-all text-left"
              >
                <span className="material-symbols-outlined text-primary mb-2">{item.icon}</span>
                <p className="text-sm font-bold text-foreground">{item.title}</p>
                <p className="text-[11px] text-muted-foreground">{item.desc}</p>
              </button>
            ))}
          </div>
        </div>
      </div>

      <footer className="mt-auto p-8 border-t border-primary/10 flex flex-col md:flex-row justify-between items-center gap-4 text-muted-foreground text-xs">
        <p>© 2024 Xenova Core Kenya. Regulated by the Insurance Regulatory Authority.</p>
        <div className="flex gap-6">
          <a className="hover:text-primary" href="#">
            Privacy Policy
          </a>
          <a className="hover:text-primary" href="#">
            Terms of Service
          </a>
          <a className="hover:text-primary" href="#">
            Help Center
          </a>
        </div>
      </footer>
    </MemberLayout>
  );
}