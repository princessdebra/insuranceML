import { useState } from "react";
import { useNavigate } from "react-router-dom";

export default function AnalystLogin() {
  const navigate = useNavigate();
  const [analystId, setAnalystId] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    localStorage.setItem("analystId", analystId.toUpperCase());
    localStorage.setItem("analystName", "Claims Analyst");
    setTimeout(() => {
      setLoading(false);
      navigate("/analyst/dashboard");
    }, 500);
  };

  return (
    <div className="relative flex min-h-screen w-full flex-col overflow-x-hidden bg-background">
      <header className="flex items-center justify-between whitespace-nowrap border-b border-primary/10 bg-card px-6 md:px-20 py-4">
        <div className="flex items-center gap-3 text-primary">
          <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center text-primary-foreground">
            <span className="material-symbols-outlined">shield</span>
          </div>
          <h2 className="text-foreground text-xl font-bold leading-tight tracking-tight">Claims Intelligence AI</h2>
        </div>
        <button onClick={() => navigate("/")} className="flex items-center gap-1 text-muted-foreground hover:text-primary text-sm font-medium">
          <span className="material-symbols-outlined text-[20px]">chevron_left</span>
          Back to Home
        </button>
      </header>

      <main className="flex-1 flex items-center justify-center p-4 md:p-8">
        <div className="w-full max-w-[1000px] grid grid-cols-1 md:grid-cols-2 bg-card rounded-xl overflow-hidden shadow-xl shadow-primary/5 border border-primary/10">
          <div className="relative hidden md:flex flex-col justify-end overflow-hidden bg-gradient-to-br from-primary via-emerald-700 to-emerald-900 p-10">
            <div className="absolute inset-0 opacity-[0.07] pointer-events-none">
              <div className="absolute inset-0" style={{ backgroundImage: "radial-gradient(circle at 2px 2px, #ffffff 1px, transparent 0)", backgroundSize: "32px 32px" }} />
            </div>
            <span className="material-symbols-outlined absolute top-10 right-10 text-primary-foreground/10 text-[160px] leading-none">support_agent</span>
            <div className="relative z-10">
              <div className="size-14 rounded-2xl bg-white/10 backdrop-blur-sm flex items-center justify-center mb-6 border border-white/20">
                <span className="material-symbols-outlined text-primary-foreground text-3xl">headset_mic</span>
              </div>
              <h1 className="text-primary-foreground text-3xl font-bold mb-2">Claims Analyst Desk</h1>
              <p className="text-white/85 text-sm max-w-xs mb-6 leading-relaxed">
                File and track claims phoned in by members, with the same AI intelligence powering the self-service portal.
              </p>
              <div className="flex items-center gap-2 text-white/90 text-sm font-medium">
                <span className="material-symbols-outlined text-[18px]">verified</span>
                Internal Staff Portal
              </div>
            </div>
          </div>

          <div className="p-8 md:p-12 flex flex-col justify-center">
            <div className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="bg-primary/10 text-primary text-[10px] font-bold px-2 py-1 rounded uppercase tracking-widest">Claims Analyst Access</span>
              </div>
              <h2 className="text-foreground text-3xl font-bold mb-2">Analyst Portal</h2>
              <p className="text-muted-foreground text-sm">Secure access for Old Mutual claims analysts and contact centre staff.</p>
            </div>
            <form className="space-y-5" onSubmit={handleSubmit}>
              <div className="flex flex-col gap-2">
                <label className="text-foreground text-sm font-semibold">Analyst ID</label>
                <div className="relative">
                  <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-[20px]">badge</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background py-3 pl-10 pr-4 text-foreground focus:border-primary focus:ring-1 focus:ring-primary outline-none transition-all placeholder:text-muted-foreground"
                    placeholder="e.g. ANL001"
                    type="text"
                    value={analystId}
                    onChange={(e) => setAnalystId(e.target.value)}
                    required
                  />
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <label className="text-foreground text-sm font-semibold">Password</label>
                <div className="relative flex items-center">
                  <span className="material-symbols-outlined absolute left-3 text-muted-foreground text-[20px]">lock</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background py-3 pl-10 pr-12 text-foreground focus:border-primary focus:ring-1 focus:ring-primary outline-none transition-all placeholder:text-muted-foreground"
                    placeholder="Enter your password"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <button className="absolute right-3 text-muted-foreground hover:text-primary transition-colors" type="button" onClick={() => setShowPassword(!showPassword)}>
                    <span className="material-symbols-outlined text-[20px]">{showPassword ? "visibility_off" : "visibility"}</span>
                  </button>
                </div>
              </div>
              <button
                className="w-full bg-primary hover:bg-primary/90 text-primary-foreground font-bold py-4 rounded-lg shadow-lg shadow-primary/20 transition-all flex items-center justify-center gap-2 mt-4 disabled:opacity-50"
                type="submit"
                disabled={loading}
              >
                {loading ? "Authenticating..." : "Access Analyst Desk"}
                {!loading && <span className="material-symbols-outlined">arrow_forward</span>}
              </button>
            </form>
            <div className="mt-8 flex items-center justify-center gap-4 grayscale opacity-60">
              <span className="material-symbols-outlined text-muted-foreground">verified_user</span>
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold">Authorized Staff Only</span>
            </div>
          </div>
        </div>
      </main>

      <footer className="py-6 px-10 text-center">
        <p className="text-muted-foreground text-[12px]">© 2024 Claims Intelligence AI. All rights reserved.</p>
      </footer>
    </div>
  );
}
