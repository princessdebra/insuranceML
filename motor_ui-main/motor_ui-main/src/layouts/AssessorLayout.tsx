import { ReactNode, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

interface AssessorLayoutProps {
  children: ReactNode;
  assessorName?: string;
  assessorId?: string;
}

const navItems = [
  { label: "Dashboard", icon: "dashboard", path: "/assessor/dashboard" },
  
  
];

export default function AssessorLayout({ children, assessorName, assessorId }: AssessorLayoutProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const name = assessorName || localStorage.getItem("assessorName") || "Assessor";
  const id = assessorId || localStorage.getItem("assessorId") || "";
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Mobile backdrop */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 md:hidden"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      {/* Sidebar -- off-canvas drawer below md, static column at md+ */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 flex-shrink-0 bg-card border-r border-border flex flex-col
          transform transition-transform duration-200 ease-out
          md:static md:translate-x-0
          ${mobileMenuOpen ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="p-6">
          <div className="flex items-center gap-3 mb-8">
            <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center text-primary-foreground">
              <span className="material-symbols-outlined">shield</span>
            </div>
            <div className="flex flex-col">
              <h2 className="text-sm font-bold uppercase tracking-wider text-primary">Claims Intelligence AI </h2>
              <span className="text-[10px] text-muted-foreground">Claims Intelligence AI </span>
            </div>
          </div>
          <div className="flex items-center gap-3 p-3 bg-background rounded-xl mb-8">
            <div className="size-10 rounded-full bg-primary/20 flex items-center justify-center text-primary font-bold text-xs">
              {name.split(" ").map(n => n[0]).join("")}
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-semibold text-foreground">{name}</span>
              <span className="text-xs text-muted-foreground">{id}</span>
            </div>
          </div>
          <nav className="space-y-1">
            {navItems.map((item) => {
              const isActive = location.pathname === item.path;
              return (
                <Link
                  key={item.label}
                  to={item.path}
                  onClick={() => setMobileMenuOpen(false)}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                    isActive
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-primary/10 hover:text-primary"
                  }`}
                >
                  <span className="material-symbols-outlined text-xl">{item.icon}</span>
                  <span className="text-sm font-medium">{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="mt-auto p-6 pt-0">
          <button
            onClick={() => {
              localStorage.removeItem("assessorId");
              localStorage.removeItem("assessorName");
              navigate("/");
            }}
            className="flex items-center gap-3 w-full px-3 py-2 text-muted-foreground hover:text-destructive transition-colors"
          >
            <span className="material-symbols-outlined text-[20px]">logout</span>
            <span className="text-sm font-medium">Logout</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-y-auto bg-background">
        {/* Header */}
        <header className="h-16 bg-card border-b border-border flex items-center justify-between px-4 sm:px-8 sticky top-0 z-10 gap-2">
          <div className="flex items-center gap-2 sm:gap-4 min-w-0">
            <button
              type="button"
              className="md:hidden flex items-center justify-center size-9 rounded-lg border border-border text-foreground shrink-0"
              aria-label={mobileMenuOpen ? "Close menu" : "Open menu"}
              onClick={() => setMobileMenuOpen((v) => !v)}
            >
              <span className="material-symbols-outlined">{mobileMenuOpen ? "close" : "menu"}</span>
            </button>
            <button onClick={() => navigate(-1)} className="hidden sm:flex items-center gap-1 text-muted-foreground hover:text-primary transition-colors text-sm font-medium shrink-0">
              <span className="material-symbols-outlined text-[20px]">chevron_left</span>
              Back
            </button>
            <span className="hidden sm:block h-4 w-px bg-border"></span>
            <h1 className="text-base sm:text-lg font-bold text-foreground truncate">Assessor Portal</h1>
          </div>
          <div className="flex items-center gap-4">
            <button className="relative text-muted-foreground hover:text-primary transition-colors">
              <span className="material-symbols-outlined">search</span>
            </button>
            <button className="relative text-muted-foreground hover:text-primary transition-colors">
              <span className="material-symbols-outlined">notifications</span>
              <span className="absolute top-0 right-0 size-2 bg-destructive rounded-full border-2 border-card"></span>
            </button>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
