import { Link } from "react-router-dom";

export default function LandingPage() {
  return (
    <div className="relative flex min-h-screen w-full flex-col overflow-x-hidden bg-background">
      {/* Navigation */}
      <header className="flex items-center justify-between whitespace-nowrap border-b border-primary/10 px-6 py-4 lg:px-20 bg-card sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-primary rounded-lg flex items-center justify-center text-primary-foreground">
            <span className="material-symbols-outlined">shield</span>
          </div>
          <h2 className="text-lg font-bold tracking-tight text-foreground">Claims Intelligence AI </h2>
        </div>
        <div className="hidden md:flex flex-1 justify-center gap-8">
          <a className="text-sm font-medium text-foreground hover:text-primary transition-colors" href="#">Solutions</a>
          <a className="text-sm font-medium text-foreground hover:text-primary transition-colors" href="#">Innovation</a>
          <a className="text-sm font-medium text-foreground hover:text-primary transition-colors" href="#">Heritage</a>
          <a className="text-sm font-medium text-foreground hover:text-primary transition-colors" href="#">Claims</a>
        </div>
        <div className="flex items-center gap-4">
          <Link to="/member/login" className="hidden sm:flex min-w-[120px] cursor-pointer items-center justify-center rounded-lg h-10 px-4 bg-primary text-primary-foreground text-sm font-bold transition-transform hover:scale-105">
            Member Login
          </Link>
          <Link to="/assessor/login" className="hidden sm:flex min-w-[120px] cursor-pointer items-center justify-center rounded-lg h-10 px-4 border border-primary text-primary text-sm font-bold transition-transform hover:scale-105 hover:bg-primary/10">
            Assessor Login
          </Link>
          <Link to="/admin/login" className="hidden sm:flex min-w-[120px] cursor-pointer items-center justify-center rounded-lg h-10 px-4 border border-border text-foreground text-sm font-bold transition-transform hover:scale-105 hover:bg-muted">
            Admin
          </Link>
        </div>
      </header>

      <main className="flex flex-col">
        {/* Hero Section */}
        <section className="relative w-full px-6 py-12 lg:px-20 lg:py-20">
          <div className="mx-auto max-w-[1280px]">
            <div className="relative overflow-hidden rounded-3xl bg-foreground min-h-[500px] flex items-center">
              <div className="absolute inset-0 z-0">
                <div className="absolute inset-0 bg-gradient-to-r from-background-dark via-background-dark/80 to-transparent z-10"></div>
                <div className="w-full h-full bg-cover bg-center" style={{ backgroundImage: "url('https://lh3.googleusercontent.com/aida-public/AB6AXuA9NvdlHopkCRdPB1vKVEVL-5hQhlxrjQrTfT7RGr_ihHw0ourUjVWd8F43s2X5OZgOqvKKWpXl6hNRBxWSbaUFu8NmkgOqiY1cldSwZEF09-gx_BAZPAeskxyehThlA0nRvLhoHBc9djN9tfVUUTucrJ_U0smb0d8ACUTx8XhicGTcsAIVCk9k2yXz5jENEDVvBJZ8cZvXvV8KgK6-VjKZfN-p8kkYzLethf7I8m5vPswzoKR3iwzrVPWIHEZiqnhRd6mUMSgqnZQ')" }} />
              </div>
              <div className="relative z-20 px-8 lg:px-16 py-12 max-w-2xl flex flex-col gap-6">
                <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/20 text-primary text-xs font-bold uppercase tracking-wider w-fit">
                  <span className="material-symbols-outlined text-sm">bolt</span>
                  Next-Gen Claims Processing 
                </span>
                <h1 className="text-primary-foreground text-4xl lg:text-6xl font-black leading-tight tracking-tight">
                  AI-Powered Claims for Kenya's Roads
                </h1>
                <p className="text-slate-300 text-lg leading-relaxed">
                  Experience the future of motor insurance with Claims Intelligence AI . Smarter risk assessment, faster approvals, and a journey built specifically for the Kenyan landscape.
                </p>
                <div className="flex flex-wrap gap-4 pt-4">
                  <Link to="/member/login" className="flex min-w-[180px] cursor-pointer items-center justify-center rounded-lg h-14 px-6 bg-primary text-primary-foreground text-base font-bold transition-all hover:bg-primary/90">
                    Experience the Future
                  </Link>
                  <button className="flex min-w-[180px] cursor-pointer items-center justify-center rounded-lg h-14 px-6 bg-white/10 text-primary-foreground border border-white/20 text-base font-bold backdrop-blur-sm transition-all hover:bg-white/20">
                    Learn More
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* AI Innovation Showcase */}
        <section className="px-6 py-16 lg:px-20 bg-card">
          <div className="mx-auto max-w-[1280px]">
            <div className="text-center mb-16">
              <h2 className="text-primary text-sm font-bold uppercase tracking-[0.2em] mb-3">AI Innovation</h2>
              <h3 className="text-3xl lg:text-5xl font-black mb-6 text-foreground">Redefining Insurance Technology</h3>
              <p className="text-muted-foreground max-w-2xl mx-auto text-lg leading-relaxed">
                Our agentic AI orchestration manages your policy lifecycle from risk assessment to renewal seamlessly.
              </p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              {[
                { icon: "timer", title: "Instant Algorithmic Claims", desc: "Policies issued in under 3 minutes using advanced data models tailored for local road conditions." },
                { icon: "photo_camera", title: "Touchless Claims Assessment", desc: "Real-time image validation for minor incidents, settling in under 2 hours via mobile-first AI analysis." },
                { icon: "psychology", title: "Agentic AI Orchestration", desc: "Autonomous lifecycle management from risk to renewal, proactively adjusting coverage." },
                { icon: "security", title: "Fraud Prevention", desc: "Advanced AI-driven security protecting your assets with multi-layer verification systems." },
              ].map((card) => (
                <div key={card.title} className="group p-8 rounded-2xl bg-background border border-primary/5 hover:border-primary/30 transition-all hover:-translate-y-2">
                  <div className="size-14 rounded-xl bg-primary/10 flex items-center justify-center mb-6 group-hover:bg-primary group-hover:text-primary-foreground transition-colors text-primary">
                    <span className="material-symbols-outlined text-3xl">{card.icon}</span>
                  </div>
                  <h4 className="text-xl font-bold mb-3 text-foreground">{card.title}</h4>
                  <p className="text-muted-foreground text-sm leading-relaxed">{card.desc}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* African Heritage Section */}
        <section className="px-6 py-16 lg:px-20 overflow-hidden">
          <div className="mx-auto max-w-[1280px]">
            <div className="flex flex-col lg:flex-row items-center gap-12 lg:gap-20">
              <div className="w-full lg:w-1/2 relative">
                <div className="absolute -top-10 -left-10 w-40 h-40 bg-primary/5 rounded-full blur-3xl"></div>
                <div className="relative z-10 rounded-3xl overflow-hidden aspect-[4/5] lg:aspect-square border-8 border-card shadow-2xl">
                  <img className="w-full h-full object-cover" alt="Confident Kenyan professional" src="https://lh3.googleusercontent.com/aida-public/AB6AXuAXcP053Gd8uIMsZ_CvifS8Arv7WuWbbn6XTjgnbzU5edVMEWbfU7pgkpxcbBDd4OdQbUIiRj-dsoyY6wwY6eLl5qIEjuZZrOCRJTPvAcCIJ5YDgfBXoFf2i7VxipTWUFfgxhjE8TkfTyxeRLZ_hBeq0B-f_iDBnkgDljKfcNgdHJ1DqnSK4L-Cs21swbMFEIY8eUjmsDMen-GxkTTvNV26DxaUboCt4fg8eTHDIjwy9J3eML0foSQoGS0dxVdeL8E7BGYJVE0Je5c" />
                </div>
                <div className="absolute -bottom-6 -right-6 lg:right-0 bg-primary p-6 rounded-2xl text-primary-foreground shadow-xl max-w-[240px]">
                  <p className="font-bold text-lg mb-1">Glocal Impact</p>
                  <p className="text-xs opacity-90 leading-relaxed">Blending global standards with Kenyan cultural insights.</p>
                </div>
              </div>
              <div className="w-full lg:w-1/2 flex flex-col gap-8">
                <div>
                  <h3 className="text-3xl lg:text-5xl font-black mb-6 leading-tight text-foreground">African Heritage Meets Global Tech</h3>
                  <p className="text-muted-foreground text-lg leading-relaxed">
                    We believe in the power of 'Glocal'. By combining Xenova legacy with deep-rooted Kenyan values and tech innovation.
                  </p>
                </div>
                <ul className="flex flex-col gap-4">
                  {["Localized risk models accounting for Nairobi traffic and rural terrains.", "Cultural motifs integrated into digital interfaces.", "Seamless integration with Kenya's leading mobile payment ecosystems."].map((text) => (
                    <li key={text} className="flex gap-4 items-start">
                      <div className="mt-1 flex-shrink-0 size-6 bg-primary/20 text-primary rounded-full flex items-center justify-center">
                        <span className="material-symbols-outlined text-sm font-bold">check</span>
                      </div>
                      <p className="text-foreground font-medium">{text}</p>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </section>

        {/* Live Analysis Section */}
        <section className="px-6 py-20 lg:px-20 bg-background-dark text-primary-foreground relative overflow-hidden">
          <div className="absolute inset-0 opacity-10 pointer-events-none">
            <div className="absolute top-0 left-0 w-full h-full" style={{ backgroundImage: "radial-gradient(circle at 2px 2px, #009476 1px, transparent 0)", backgroundSize: "40px 40px" }} />
          </div>
          <div className="mx-auto max-w-[1280px] relative z-10">
            <div className="flex flex-col lg:flex-row items-center gap-16">
              <div className="w-full lg:w-1/2">
                <h2 className="text-primary text-sm font-bold uppercase tracking-[0.2em] mb-4">Live Analysis</h2>
                <h3 className="text-3xl lg:text-5xl font-black mb-6 leading-tight">AI at Your Fingertips</h3>
                <p className="text-slate-400 text-lg leading-relaxed mb-8">
                  See how our AI works in real-time. Simply point your mobile camera at your vehicle, and our engine instantly calculates risk profiles.
                </p>
                <div className="grid grid-cols-2 gap-4">
                  <div className="p-6 rounded-2xl bg-white/5 border border-white/10 backdrop-blur-sm">
                    <p className="text-primary font-black text-2xl mb-1">98%</p>
                    <p className="text-xs uppercase font-bold text-slate-400 tracking-wider">Accuracy Rate</p>
                  </div>
                  <div className="p-6 rounded-2xl bg-white/5 border border-white/10 backdrop-blur-sm">
                    <p className="text-primary font-black text-2xl mb-1">&lt;180s</p>
                    <p className="text-xs uppercase font-bold text-slate-400 tracking-wider">Processing Time</p>
                  </div>
                </div>
              </div>
              <div className="w-full lg:w-1/2 flex justify-center">
                <div className="relative w-72 h-[600px] bg-slate-800 rounded-[3rem] border-8 border-slate-700 shadow-2xl overflow-hidden">
                  <div className="absolute top-0 w-full h-6 bg-slate-700 flex justify-center items-end pb-1">
                    <div className="w-20 h-3 bg-slate-800 rounded-full"></div>
                  </div>
                  <div className="w-full h-full bg-slate-900 relative overflow-hidden p-6 pt-10">
                    <div className="flex justify-between items-center mb-6">
                      <span className="material-symbols-outlined text-primary">menu</span>
                      <div className="text-[10px] font-bold text-primary-foreground">CAR SCANNER PRO</div>
                      <span className="material-symbols-outlined text-primary">account_circle</span>
                    </div>
                    <div className="relative w-full aspect-square rounded-2xl bg-slate-800 mb-6 overflow-hidden flex items-center justify-center">
                      <div className="absolute inset-0 w-full h-full bg-cover bg-center" style={{ backgroundImage: "url('https://lh3.googleusercontent.com/aida-public/AB6AXuD2V9HBhsB4RGI_nKJroFgMHW_fJbhC1g8gEZVW0wBt-hKArFY1OUbECvePUb_Blik_-du0et7dtOR53sS22IifZnFwoiFQRdNoAdtWhgfbdNxy6CBBOf4LjYBZ5MUwSz2n-tBOubLJN7VJHvZWIXZHPf777tRGf4hB0OZV24YlmOznsSG0E66LbyhK1IwJ_xKAF3VF3YLF8fb3jzS43fyY2PEt60tk0oDwO41pstvU2WAUb6P2OzWmQ-6FDmBQ8braGZ88A28_VcY')" }} />
                      <div className="absolute inset-0 border-2 border-primary/50 flex flex-col justify-center items-center">
                        <div className="w-full h-0.5 bg-primary/80 animate-pulse"></div>
                      </div>
                      <div className="absolute bottom-2 left-2 px-2 py-1 bg-primary text-[8px] font-bold rounded text-primary-foreground">OBJECT DETECTED</div>
                    </div>
                    <div className="flex flex-col gap-3">
                      <div className="p-3 bg-white/10 rounded-xl flex items-center justify-between">
                        <span className="text-[10px] font-medium text-slate-300">Vehicle Make</span>
                        <span className="text-[10px] font-bold text-primary">TOYOTA RAV4</span>
                      </div>
                      <div className="p-3 bg-white/10 rounded-xl">
                        <div className="flex justify-between mb-1">
                          <span className="text-[10px] font-medium text-slate-300">AI Risk Score</span>
                          <span className="text-[10px] font-bold text-primary">Low (0.12)</span>
                        </div>
                        <div className="w-full h-1 bg-slate-700 rounded-full overflow-hidden">
                          <div className="w-1/4 h-full bg-primary"></div>
                        </div>
                      </div>
                      <button className="w-full py-3 bg-primary rounded-xl text-xs font-black uppercase mt-4 text-primary-foreground">Generate Quote</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="bg-background px-6 py-16 lg:px-20 border-t border-primary/10">
        <div className="mx-auto max-w-[1280px]">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-12 mb-12">
            <div className="flex flex-col gap-6">
              <div className="flex items-center gap-3">
                <div className="text-primary size-8">
                  <svg fill="currentColor" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
                    <path d="M24 4C25.7818 14.2173 33.7827 22.2182 44 24C33.7827 25.7818 25.7818 33.7827 24 44C22.2182 33.7827 14.2173 25.7818 4 24C14.2173 22.2182 22.2182 14.2173 24 4Z" />
                  </svg>
                </div>
                <h2 className="text-lg font-bold tracking-tight text-foreground">Claims Intelligence AI </h2>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">
                Pioneering insurance solutions in Kenya for over a century. Now leading the charge into the future with Agentic AI.
              </p>
            </div>
            <div>
              <h4 className="font-bold mb-6 text-foreground">Solutions</h4>
              <ul className="flex flex-col gap-3">
                {["Motor Private", "Motor Commercial", "Fleet Management", "Roadside Assistance"].map(s => (
                  <li key={s}><a className="text-sm text-muted-foreground hover:text-primary" href="#">{s}</a></li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="font-bold mb-6 text-foreground">Innovation</h4>
              <ul className="flex flex-col gap-3">
                {["Claims Intelligence AI", "Smart Claims", "Risk Analytics", "Tech Partners"].map(s => (
                  <li key={s}><a className="text-sm text-muted-foreground hover:text-primary" href="#">{s}</a></li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="font-bold mb-6 text-foreground">Support</h4>
              <ul className="flex flex-col gap-3">
                {["Find an Agent", "Claim Status", "Help Center", "Contact Us"].map(s => (
                  <li key={s}><a className="text-sm text-muted-foreground hover:text-primary" href="#">{s}</a></li>
                ))}
              </ul>
            </div>
          </div>
          <div className="pt-8 border-t border-primary/5 flex flex-col md:flex-row justify-between items-center gap-4">
            <p className="text-xs text-muted-foreground">© 2024 Xenova. All rights reserved.</p>
            <div className="flex gap-6">
              <a className="text-xs text-muted-foreground hover:text-primary" href="#">Privacy Policy</a>
              <a className="text-xs text-muted-foreground hover:text-primary" href="#">Terms of Service</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
