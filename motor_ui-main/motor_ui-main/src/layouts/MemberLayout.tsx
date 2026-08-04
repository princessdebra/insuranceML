import { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

interface MemberLayoutProps {
  children: ReactNode;
  memberName?: string;
  memberId?: string;
}

const navItems = [
  { label: "Dashboard", icon: "dashboard", path: "/member/dashboard" },
  { label: "My Policies", icon: "shield_with_heart", path: "/member/policies" },
  
];

export default function MemberLayout({ children, memberName, memberId }: MemberLayoutProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const name = memberName || localStorage.getItem("memberName") || "Member";
  const id = memberId || localStorage.getItem("memberId") || "";

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Sidebar */}
      <aside className="w-64 flex-shrink-0 border-r border-primary/10 bg-card flex flex-col justify-between p-4">
        <div className="flex flex-col gap-8">
          {/* Branding */}
          <div className="flex items-center gap-3 px-2">
            <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center text-primary-foreground">
              <span className="material-symbols-outlined">shield</span>
            </div>
            <h2 className="text-xl font-bold tracking-tight text-foreground">Claims Intelligence AI </h2>
          </div>
          {/* User */}
          <div className="flex items-center gap-3 px-2">
            <div className="size-10 rounded-full bg-primary/20 flex items-center justify-center text-primary font-bold text-sm">
              {name.split(" ").map(n => n[0]).join("")}
            </div>
            <div className="flex flex-col">
              <h1 className="text-sm font-semibold leading-tight text-foreground">{name}</h1>
              <p className="text-xs text-primary font-medium">{id}</p>
            </div>
          </div>
          {/* Nav */}
          <nav className="flex flex-col gap-1">
            {navItems.map((item) => {
              const isActive = location.pathname === item.path || location.pathname.startsWith(item.path + "/");
              return (
                <Link
                  key={item.label}
                  to={item.path}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-primary/10 hover:text-primary"
                  }`}
                >
                  <span className="material-symbols-outlined text-[20px]">{item.icon}</span>
                  <span className="text-sm font-medium">{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="px-2 pb-4">
          <button
            onClick={() => {
              localStorage.removeItem("memberId");
              localStorage.removeItem("memberName");
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
      <main className="flex-1 flex flex-col overflow-y-auto">
        {/* Header */}
        <header className="h-16 border-b border-primary/10 bg-card/80 backdrop-blur-md flex items-center justify-between px-8 sticky top-0 z-10">
          <div className="flex items-center gap-4 flex-1 max-w-xl">
            <button onClick={() => navigate(-1)} className="flex items-center gap-1 text-muted-foreground hover:text-primary transition-colors text-sm font-medium">
              <span className="material-symbols-outlined text-[20px]">chevron_left</span>
              Back
            </button>
            <div className="relative w-full">
              <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-[20px]">search</span>
              <input
                className="w-full pl-10 pr-4 py-2 bg-background border-none rounded-lg focus:ring-2 focus:ring-primary/50 text-sm"
                placeholder="Search policies, claims or help..."
                type="text"
              />
            </div>
          </div>
          <div className="flex items-center gap-6">
            <button className="relative text-muted-foreground hover:text-primary transition-colors">
              <span className="material-symbols-outlined text-[24px]">notifications</span>
              <span className="absolute top-0 right-0 size-2 bg-destructive rounded-full border-2 border-card"></span>
            </button>
            <div className="h-8 w-px bg-primary/10"></div>
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium hidden md:block">Nairobi, Kenya</span>
              <span className="material-symbols-outlined text-primary">location_on</span>
            </div>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
