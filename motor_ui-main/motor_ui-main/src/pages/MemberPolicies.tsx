import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import MemberLayout from "@/layouts/MemberLayout";
import { getMemberPolicies } from "@/lib/api";

export default function MemberPolicies() {
  const navigate = useNavigate();
  const [policies, setPolicies] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const memberId = localStorage.getItem("memberId");
    if (!memberId) { navigate("/member/login"); return; }
    getMemberPolicies(memberId).then((d) => { if (d.success) setPolicies(d.data); setLoading(false); }).catch(() => setLoading(false));
  }, [navigate]);

  if (loading) return <MemberLayout><div className="flex items-center justify-center h-full"><p className="text-muted-foreground">Loading...</p></div></MemberLayout>;

  return (
    <MemberLayout>
      <div className="p-8 max-w-6xl mx-auto">
        <div className="flex justify-between items-end mb-8">
          <div>
            <nav className="flex mb-2">
              <ol className="flex items-center space-x-2 text-xs text-muted-foreground">
                <li><Link to="/member/dashboard" className="hover:text-primary">Dashboard</Link></li>
                <li><span className="material-symbols-outlined text-[10px]">chevron_right</span></li>
                <li className="text-primary font-medium">My Policies</li>
              </ol>
            </nav>
            <h1 className="text-3xl font-black text-foreground tracking-tight">My Policies</h1>
            <p className="text-muted-foreground mt-1">Manage and view your active insurance policies in Kenya.</p>
          </div>
          <button className="flex items-center gap-2 px-5 py-2.5 bg-primary/10 text-primary hover:bg-primary/20 rounded-lg font-bold text-sm transition-colors">
            <span className="material-symbols-outlined">post_add</span>
            Add New Policy
          </button>
        </div>

        <div className="grid grid-cols-1 gap-8">
          {policies.map((policy) => (
            <div key={policy.policy_id} className="bg-card rounded-xl overflow-hidden shadow-sm border border-border group">
              <div className="flex flex-col lg:flex-row">
                <div className="lg:w-1/3 relative overflow-hidden min-h-[240px]">
                  <div className="absolute inset-0 bg-gradient-to-br from-primary/40 to-primary/10 z-10"></div>
                  <div className="absolute inset-0 bg-cover bg-center transition-transform duration-700 group-hover:scale-105" style={{ backgroundImage: "url('https://lh3.googleusercontent.com/aida-public/AB6AXuDBXFwf0mhfybepS-ASRKtko-qDIB-92k7s0SuQsRwK9sJyXSZ02W4yMCyk7jCWv4f6E2AaS3Ul8EeY2uEdpq66JomjqoqI-YJnCd2_qzUvQMw8T4aQZPovgqT0PVsTyk_xzMC3UrzwmVYrZ7Ju520CzWIOE3JOtok-5ACwt2kxndMuBlX1Q0PkajXTDLUluThaV5DVnJp8UciEgw2_dXUh2KDiz-Jd_RLQsnTWEAMdanLNbuPzz7soZ7QGaum9PycQhMeyOwJjm78')" }} />
                  <div className="absolute bottom-4 left-4 z-20">
                    <span className="bg-primary text-primary-foreground text-[10px] font-bold px-2 py-1 rounded-md uppercase tracking-wider">{policy.cover_type}</span>
                  </div>
                </div>
                <div className="flex-1 p-6 lg:p-8 flex flex-col justify-between">
                  <div className="flex justify-between items-start mb-6">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-bold text-primary uppercase">Active Policy</span>
                        <span className="size-1.5 rounded-full bg-primary"></span>
                        <span className="text-xs font-medium text-muted-foreground">{policy.policy_type} Insurance</span>
                      </div>
                      <h3 className="text-2xl font-bold text-foreground">Policy: {policy.policy_number}</h3>
                    </div>
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-100 text-emerald-700 text-xs font-bold">
                      <span className="size-2 rounded-full bg-emerald-500 animate-pulse"></span>
                      {policy.status}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-6 py-6 border-y border-border">
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">Sum Insured</p>
                      <p className="text-lg font-bold text-foreground">KES {Number(policy.sum_insured).toLocaleString()}</p>
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">Policy Type</p>
                      <p className="text-lg font-bold text-foreground capitalize">{policy.policy_type}</p>
                    </div>
                    <div className="space-y-1 col-span-2">
                      <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">Period of Cover</p>
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-bold text-foreground">{policy.start_date}</p>
                        <span className="material-symbols-outlined text-muted-foreground text-sm">trending_flat</span>
                        <p className="text-sm font-bold text-foreground">{policy.end_date}</p>
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-4 mt-8">
                    <div className="flex gap-3">
                      <button className="px-5 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-bold shadow-sm shadow-primary/20 hover:brightness-110 transition-all">View Details</button>
                      <button className="px-5 py-2.5 bg-background text-foreground rounded-lg text-sm font-bold flex items-center gap-2 hover:bg-muted transition-all border border-border">
                        <span className="material-symbols-outlined text-lg">download</span>
                        Download Wording
                      </button>
                    </div>
                    <button className="px-4 py-2 text-primary text-sm font-bold hover:underline">Renew Policy</button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Help Card */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-8">
          <div className="md:col-span-2 bg-card p-6 rounded-xl border border-border">
            <h4 className="text-lg font-bold mb-4 text-foreground">Policy Breakdown</h4>
            <div className="space-y-4">
              {[
                { icon: "directions_car", label: "Vehicle Registration", value: "KDL 420P" },
                { icon: "verified_user", label: "Underwriter", value: "Xenova Core Life Assurance" },
                { icon: "event_repeat", label: "Payment Frequency", value: "Annual" },
                { icon: "support_agent", label: "Agent / Broker", value: "Direct Channel" },
              ].map((item, i) => (
                <div key={item.label} className={`flex justify-between items-center py-3 ${i < 3 ? "border-b border-border/50" : ""}`}>
                  <div className="flex items-center gap-3">
                    <span className="material-symbols-outlined text-primary">{item.icon}</span>
                    <span className="text-sm font-medium text-foreground">{item.label}</span>
                  </div>
                  <span className="text-sm font-bold text-foreground">{item.value}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="bg-primary rounded-xl p-6 text-primary-foreground flex flex-col justify-between relative overflow-hidden shadow-lg shadow-primary/20">
            <div className="absolute -right-8 -top-8 size-40 bg-white/10 rounded-full blur-3xl"></div>
            <div className="relative z-10">
              <span className="material-symbols-outlined text-3xl mb-4">contact_support</span>
              <h4 className="text-xl font-black mb-2 leading-tight">Need assistance with your policy?</h4>
              <p className="text-white/80 text-sm leading-relaxed mb-6">Our support team is available 24/7.</p>
            </div>
            <div className="relative z-10 space-y-3">
              <a className="flex items-center gap-2 text-sm font-bold" href="tel:+254711010000">
                <span className="material-symbols-outlined text-lg">call</span>
                +254 711 010 000
              </a>
              <button className="w-full bg-card text-primary font-bold py-2.5 rounded-lg text-sm hover:bg-background transition-colors">
                Chat with AI Assistant
              </button>
            </div>
          </div>
        </div>
      </div>

      <footer className="p-8 text-center text-muted-foreground text-xs">
        <p>© 2024 Xenova Core Kenya. All rights reserved.</p>
      </footer>
    </MemberLayout>
  );
}
