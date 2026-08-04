import { useState } from "react";
import { useNavigate } from "react-router-dom";

const DUMMY_ADMIN = { id: "ADMIN001", password: "admin123" };

export default function AdminLogin() {
  const navigate = useNavigate();
  const [adminId, setAdminId] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    setTimeout(() => {
      if (adminId.toUpperCase() === DUMMY_ADMIN.id && password === DUMMY_ADMIN.password) {
        localStorage.setItem("adminId", adminId.toUpperCase());
        navigate("/admin/dashboard");
      } else {
        setError("Invalid Admin ID or Password. Use ADMIN001 / admin123");
      }
      setLoading(false);
    }, 500);
  };

  return (
    <div className="relative flex min-h-screen w-full flex-col overflow-x-hidden bg-background">
      <header className="flex items-center justify-between whitespace-nowrap border-b border-primary/10 bg-card px-6 md:px-20 py-4">
        <div className="flex items-center gap-3 text-primary">
          <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center text-primary-foreground">
            <span className="material-symbols-outlined">shield</span>
          </div>
          <h2 className="text-foreground text-xl font-bold leading-tight tracking-tight">Claims Intelligence AI </h2>
        </div>
        <button onClick={() => navigate("/")} className="flex items-center gap-1 text-muted-foreground hover:text-primary text-sm font-medium">
          <span className="material-symbols-outlined text-[20px]">chevron_left</span>
          Back to Home
        </button>
      </header>

      <main className="flex-1 flex items-center justify-center p-4 md:p-8">
        <div className="w-full max-w-[1000px] grid grid-cols-1 md:grid-cols-2 bg-card rounded-xl overflow-hidden shadow-xl shadow-primary/5 border border-primary/10">
          <div className="relative hidden md:block overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-t from-primary/80 to-transparent z-10"></div>
            <div className="h-full w-full bg-cover bg-center" style={{ backgroundImage: "url('https://lh3.googleusercontent.com/aida-public/AB6AXuA9NvdlHopkCRdPB1vKVEVL-5hQhlxrjQrTfT7RGr_ihHw0ourUjVWd8F43s2X5OZgOqvKKWpXl6hNRBxWSbaUFu8NmkgOqiY1cldSwZEF09-gx_BAZPAeskxyehThlA0nRvLhoHBc9djN9tfVUUTucrJ_U0smb0d8ACUTx8XhicGTcsAIVCk9k2yXz5jENEDVvBJZ8cZvXvV8KgK6-VjKZfN-p8kkYzLethf7I8m5vPswzoKR3iwzrVPWIHEZiqnhRd6mUMSgqnZQ')" }} />
            <div className="absolute bottom-0 left-0 p-10 z-20">
              <h1 className="text-primary-foreground text-3xl font-bold mb-2">Fraud Detection Console</h1>
              <p className="text-white/90 text-sm max-w-xs mb-6">AI-powered claims analysis and fraud detection for administrators.</p>
              <div className="flex items-center gap-2 text-white/90 text-sm font-medium">
                <span className="material-symbols-outlined text-[18px]">admin_panel_settings</span>
                Administrator Access Only
              </div>
            </div>
          </div>

          <div className="p-8 md:p-12 flex flex-col justify-center">
            <div className="mb-8">
              <div className="flex items-center gap-2 mb-4">
                <span className="bg-primary/10 text-primary text-[10px] font-bold px-2 py-1 rounded uppercase tracking-widest">Admin Access</span>
              </div>
              <h2 className="text-foreground text-3xl font-bold mb-2">Admin Portal</h2>
              <p className="text-muted-foreground text-sm">Secure access for system administrators.</p>
            </div>
            {error && (
              <div className="mb-4 p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-sm">{error}</div>
            )}
            <form className="space-y-5" onSubmit={handleSubmit}>
              <div className="flex flex-col gap-2">
                <label className="text-foreground text-sm font-semibold">Admin ID</label>
                <div className="relative">
                  <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-[20px]">admin_panel_settings</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background py-3 pl-10 pr-4 text-foreground focus:border-primary focus:ring-1 focus:ring-primary outline-none transition-all placeholder:text-muted-foreground"
                    placeholder="e.g. ADMIN001"
                    type="text"
                    value={adminId}
                    onChange={(e) => setAdminId(e.target.value)}
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
                    required
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
                {loading ? "Authenticating..." : "Access Admin Console"}
                {!loading && <span className="material-symbols-outlined">arrow_forward</span>}
              </button>
            </form>
            <div className="mt-8 flex items-center justify-center gap-4 grayscale opacity-60">
              <span className="material-symbols-outlined text-muted-foreground">verified_user</span>
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold">Authorized Access Only</span>
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
