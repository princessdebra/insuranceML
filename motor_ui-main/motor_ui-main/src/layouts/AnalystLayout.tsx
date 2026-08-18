import { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

interface AnalystLayoutProps {
  children: ReactNode;
  analystName?: string;
  analystId?: string;
}

const navItems = [
  { label: "Dashboard", icon: "dashboard", path: "/analyst/dashboard" },
  { label: "File a Claim", icon: "add_call", path: "/analyst/file-claim" },
];

export default function AnalystLayout({ children, analystName, analystId }: AnalystLayoutProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const name = analystName || localStorage.getItem("analystName") || "Claims Analyst";
  const id = analystId || localStorage.getItem("analystId") || "";

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Sidebar */}
      <aside className="w-64 flex-shrink-0 bg-card border-r border-border flex flex-col">
        <div className="p-6">
          <div className="flex items-center gap-3 mb-8">
            <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center text-primary-foreground">
              <span className="material-symbols-outlined">shield</span>
            </div>
            <div className="flex flex-col">
              <h2 className="text-sm font-bold uppercase tracking-wider text-primary">Claims Intelligence AI</h2>
              <span className="text-[10px] text-muted-foreground">Analyst Desk</span>
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
              localStorage.removeItem("analystId");
              localStorage.removeItem("analystName");
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
        <header className="h-16 bg-card border-b border-border flex items-center justify-between px-8 sticky top-0 z-10">
          <div className="flex items-center gap-4">
            <button onClick={() => navigate(-1)} className="flex items-center gap-1 text-muted-foreground hover:text-primary transition-colors text-sm font-medium">
              <span className="material-symbols-outlined text-[20px]">chevron_left</span>
              Back
            </button>
            <span className="h-4 w-px bg-border"></span>
            <h1 className="text-lg font-bold text-foreground">Analyst Portal</h1>
          </div>
          <div className="flex items-center gap-4">
            <Link
              to="/analyst/file-claim"
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-bold hover:bg-primary/90 transition-colors shadow-sm shadow-primary/20"
            >
              <span className="material-symbols-outlined text-[18px]">add_call</span>
              File New Claim
            </Link>
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
