import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { getMemberDetails } from "@/lib/api";

export default function MemberLogin() {
  const navigate = useNavigate();
  const [memberId, setMemberId] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const id = memberId.toUpperCase().startsWith("MEM") ? memberId.toUpperCase() : memberId;
      const data = await getMemberDetails(id);
      
      // The API returns { success: true, data: { member_id, name, ... } }
      if (data.success && data.data) {
        localStorage.setItem("memberId", data.data.member_id);
        localStorage.setItem("memberName", data.data.name);
        localStorage.setItem("memberData", JSON.stringify(data));
        navigate("/member/dashboard");
      } else {
        setError("Invalid member ID. Please try again.");
      }
    } catch {
      setError("Could not connect to server. Please try again.");
    } finally {
      setLoading(false);
    }
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
          {/* Left Side */}
          <div className="relative hidden md:block overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-t from-primary/80 to-transparent z-10"></div>
            <div className="h-full w-full bg-cover bg-center" style={{ backgroundImage: "url('https://lh3.googleusercontent.com/aida-public/AB6AXuAQqAOwXvoWppdB9SFFTVgddfF-gOwG4llfgbmEEbtAxdAgs_0f0gJR_QIL7A88YEJYj0hzREiMBFKOh9sEVNFJHQA3hXib5w9Vy1Id0a9nzM64_njAtydnO1Owksn_JX44KmCf2VIUKe5yhsM_y_V-fuCNAKWgtfSlZxie-HLtHCurb9ZQEIyb_00hXiqDwdbIJlaAUj35J8HeIssqgpnQeGd6cxVbwFEL6RfnoP982uf4uacuxfZUMlivNCOZhg4JrbtgMBav7E8')" }} />
            <div className="absolute bottom-0 left-0 p-10 z-20">
              <h1 className="text-primary-foreground text-3xl font-bold mb-2">Claims Intelligence AI </h1>
              <p className="text-white/90 text-sm max-w-xs">Experience seamless motor insurance management with our intelligent Claims platform.</p>
            </div>
          </div>

          {/* Right Side */}
          <div className="p-8 md:p-12 flex flex-col justify-center">
            <div className="mb-8">
              <h2 className="text-foreground text-3xl font-bold mb-2">Welcome Back</h2>
              <p className="text-muted-foreground text-sm">Secure login for Claims Intelligence AI members in Kenya.</p>
            </div>
            <form className="space-y-5" onSubmit={handleSubmit}>
              <div className="flex flex-col gap-2">
                <label className="text-foreground text-sm font-semibold">Member ID or Email</label>
                <div className="relative">
                  <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-[20px]">person</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background py-3 pl-10 pr-4 text-foreground focus:border-primary focus:ring-1 focus:ring-primary outline-none transition-all placeholder:text-muted-foreground"
                    placeholder="e.g. MEM0001 or email@address.com"
                    type="text"
                    value={memberId}
                    onChange={(e) => setMemberId(e.target.value)}
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
                <div className="flex justify-end">
                  <a className="text-primary text-xs font-semibold hover:underline" href="#">Forgot password?</a>
                </div>
              </div>
              {error && <p className="text-destructive text-sm">{error}</p>}
              <button
                className="w-full bg-primary hover:bg-primary/90 text-primary-foreground font-bold py-4 rounded-lg shadow-lg shadow-primary/20 transition-all flex items-center justify-center gap-2 mt-4 disabled:opacity-50"
                type="submit"
                disabled={loading}
              >
                {loading ? "Signing In..." : "Sign In"}
                {!loading && <span className="material-symbols-outlined">arrow_forward</span>}
              </button>
            </form>
            <div className="mt-10 pt-6 border-t border-border">
              <p className="text-center text-muted-foreground text-sm">
                Not a member yet?{" "}
                <a className="text-primary font-bold hover:underline" href="#">Request a Quote</a>
              </p>
            </div>
            <div className="mt-8 flex items-center justify-center gap-4 grayscale opacity-60">
              <span className="material-symbols-outlined text-muted-foreground">verified_user</span>
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold">256-bit SSL Secure</span>
            </div>
          </div>
        </div>
      </main>

      <footer className="py-6 px-10 text-center">
        <p className="text-muted-foreground text-[12px]">© 2024 Xenova Core Kenya. All rights reserved. Licensed Financial Services Provider.</p>
      </footer>
    </div>
  );
}
