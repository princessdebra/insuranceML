import { useState } from "react";
import { AdminAnalyticsOverview } from "@/lib/api";

/**
 * System-wide oversight â€” true totals across every claim (not just the
 * current page of the claims table below it): risk distribution, final
 * decisions, business-rule/relationship trigger frequency, AI-ROL activity,
 * and a claims-per-day trend. Replaces the old page-scoped "stats" cards,
 * which only ever reflected whatever 20 claims happened to be on screen.
 */
export default function OversightPanel({
  data,
  loading,
}: {
  data: AdminAnalyticsOverview | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="bg-card p-10 rounded-2xl border border-border shadow-sm mb-10 text-center text-muted-foreground animate-pulse">
        Loading system oversight...
      </div>
    );
  }
  if (!data || !data.success) {
    return (
      <div className="bg-card p-10 rounded-2xl border border-border shadow-sm mb-10 text-center text-muted-foreground">
        Couldn't load system oversight data.
      </div>
    );
  }

  const risk = data.risk_distribution || {};
  const riskTotal = (risk.low || 0) + (risk.medium || 0) + (risk.high || 0) + (risk.unknown || 0);
  const riskSegments = [
    { key: "low", label: "Low", count: risk.low || 0, className: "bg-primary", color: "hsl(var(--primary))" },
    { key: "medium", label: "Medium", count: risk.medium || 0, className: "bg-amber-500", color: "hsl(38 92% 50%)" },
    { key: "high", label: "High", count: risk.high || 0, className: "bg-destructive", color: "hsl(var(--destructive))" },
  ];
  // Conic-gradient stops for the donut below -- each segment's share of the
  // circle, in order, using the same theme color tokens as everywhere else
  // (hsl(var(--primary)) etc.) so it stays consistent with dark mode.
  let cumulative = 0;
  const donutStops = riskTotal > 0
    ? riskSegments
        .filter((s) => s.count > 0)
        .map((s) => {
          const start = (cumulative / riskTotal) * 360;
          cumulative += s.count;
          const end = (cumulative / riskTotal) * 360;
          return `${s.color} ${start}deg ${end}deg`;
        })
    : [];

  const decisions = data.decision_distribution || {};
  const decisionTiles = [
    { key: "PAY", label: "Paid", count: decisions.PAY || 0, textClass: "text-emerald-600", bgClass: "bg-emerald-500/10", icon: "paid" },
    { key: "DENY", label: "Denied", count: decisions.DENY || 0, textClass: "text-destructive", bgClass: "bg-destructive/10", icon: "block" },
    { key: "ESCALATE", label: "Escalated", count: decisions.ESCALATE || 0, textClass: "text-amber-600", bgClass: "bg-amber-500/10", icon: "priority_high" },
  ];
  const undecided = Math.max(0, data.total_claims - (decisions.PAY || 0) - (decisions.DENY || 0) - (decisions.ESCALATE || 0));

  const heroTiles = [
    { label: "Total Claims", value: data.total_claims.toLocaleString(), icon: "folder_open", accent: "from-primary/15 to-primary/5", iconColor: "text-primary", note: `${data.claims_analyzed} fully analyzed` },
    { label: "Active Assessments", value: data.active_assessments.toLocaleString(), icon: "pending_actions", accent: "from-blue-500/15 to-blue-500/5", iconColor: "text-blue-500", note: "currently in progress" },
    { label: "Pending Review", value: data.pending_review.toLocaleString(), icon: "visibility", accent: "from-amber-500/15 to-amber-500/5", iconColor: "text-amber-500", note: "awaiting assessor action" },
    { label: "Completed", value: data.completed_assessments.toLocaleString(), icon: "task_alt", accent: "from-emerald-500/15 to-emerald-500/5", iconColor: "text-emerald-600", note: "assessments closed out" },
    { label: "Overdue", value: data.overdue_assessments.toLocaleString(), icon: "warning", accent: "from-destructive/15 to-destructive/5", iconColor: "text-destructive", note: data.overdue_assessments > 0 ? "needs attention" : "all on track" },
    { label: "Claim Value", value: `KES ${(data.claim_value_under_assessment / 1_000_000).toFixed(1)}M`, icon: "payments", accent: "from-violet-500/15 to-violet-500/5", iconColor: "text-violet-500", note: "under active assessment" },
  ];

  return (
    <div className="space-y-6 mb-10">
      {/* Hero KPI band */}
      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
        {heroTiles.map((t) => (
          <div key={t.label} className="relative overflow-hidden rounded-2xl border border-border bg-card shadow-sm p-5 hover:shadow-md transition-shadow">
            <div className={`inline-flex items-center justify-center size-10 rounded-xl bg-gradient-to-br ${t.accent}`}>
              <span className={`material-symbols-outlined text-[20px] ${t.iconColor}`}>{t.icon}</span>
            </div>
            <p className="text-2xl font-black text-foreground mt-4 tabular-nums leading-none">{t.value}</p>
            <p className="text-xs font-bold text-foreground/80 mt-1.5">{t.label}</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">{t.note}</p>
          </div>
        ))}
      </div>

      {/* Secondary stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <span className="material-symbols-outlined text-blue-500 bg-blue-500/10 p-2 rounded-lg">analytics</span>
          </div>
          <p className="text-sm font-medium text-muted-foreground">Avg Risk Score</p>
          <p className="text-3xl font-bold mt-1 text-foreground">{data.avg_risk_score}<span className="text-sm font-normal text-muted-foreground ml-1">/100</span></p>
        </div>
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <span className="material-symbols-outlined text-muted-foreground bg-muted p-2 rounded-lg">hourglass_empty</span>
          </div>
          <p className="text-sm font-medium text-muted-foreground">Awaiting Decision</p>
          <p className="text-3xl font-bold mt-1 text-foreground">{undecided}</p>
        </div>
      </div>

      {/* Risk distribution + decisions */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm">
          <h3 className="text-sm font-bold text-foreground mb-1">Risk Distribution</h3>
          <p className="text-xs text-muted-foreground mb-4">Across all {riskTotal} scored claims</p>
          {riskTotal > 0 ? (
            <div className="flex items-center gap-6">
              <div
                className="relative size-28 rounded-full shrink-0"
                style={{ background: `conic-gradient(${donutStops.join(", ")})` }}
              >
                <div className="absolute inset-[10px] rounded-full bg-card flex flex-col items-center justify-center">
                  <span className="text-lg font-black text-foreground tabular-nums leading-none">{riskTotal}</span>
                  <span className="text-[9px] text-muted-foreground font-bold uppercase mt-0.5">Claims</span>
                </div>
              </div>
              <div className="flex-1 space-y-2.5">
                {riskSegments.map((s) => (
                  <div key={s.key} className="flex items-center gap-2 text-xs">
                    <span className={`size-2.5 rounded-full shrink-0 ${s.className}`} />
                    <span className="text-muted-foreground">{s.label}</span>
                    <span className="font-bold text-foreground tabular-nums ml-auto">{s.count}</span>
                    <span className="text-muted-foreground text-[10px] w-9 text-right">
                      {riskTotal > 0 ? Math.round((s.count / riskTotal) * 100) : 0}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground italic">No scored claims yet.</p>
          )}
        </div>

        <div className="bg-card p-6 rounded-2xl border border-border shadow-sm">
          <h3 className="text-sm font-bold text-foreground mb-1">Final Decisions</h3>
          <p className="text-xs text-muted-foreground mb-4">Human triage outcomes recorded to date</p>
          <div className="grid grid-cols-3 gap-3">
            {decisionTiles.map((d) => (
              <div key={d.key} className={`rounded-xl p-3 ${d.bgClass}`}>
                <span className={`material-symbols-outlined text-[18px] ${d.textClass}`}>{d.icon}</span>
                <p className={`text-2xl font-bold tabular-nums mt-1 ${d.textClass}`}>{d.count}</p>
                <p className="text-[10px] font-bold uppercase text-muted-foreground">{d.label}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <ClaimsOverTimeChart data={data.claims_over_time} />

      {/* Rule / capability trigger frequency */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <TriggerFrequencyList
          title="Business Rules Triggered"
          subtitle="Most frequently firing rules across all claims"
          items={data.business_rules_trigger_frequency.map((r) => ({ label: r.rule_id.replace(/_/g, " "), count: r.count }))}
          emptyText="No business rules have fired yet."
        />
        <TriggerFrequencyList
          title="AI-ROL Activity by Capability"
          subtitle="Recommendation volume per AI capability"
          items={Object.entries(data.ai_rol_activity_by_capability).map(([k, v]) => ({ label: k.replace(/_/g, " "), count: v }))}
          emptyText="No AI-ROL activity recorded yet."
        />
      </div>

      {/* Relationship + similarity summary */}
      <div className="bg-card p-6 rounded-2xl border border-border shadow-sm">
        <h3 className="text-sm font-bold text-foreground mb-1">Relationship & Similarity Signals</h3>
        <p className="text-xs text-muted-foreground mb-4">Graph-based relationship analysis and narrative-similarity search, across all analyzed claims</p>
        <div className="flex flex-wrap gap-6">
          <div>
            <p className="text-2xl font-bold text-foreground tabular-nums">{data.relationship_findings.claims_with_findings}</p>
            <p className="text-[10px] font-bold uppercase text-muted-foreground">Claims with hidden relationships</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-foreground tabular-nums">{data.narrative_similarity.claims_with_matches}</p>
            <p className="text-[10px] font-bold uppercase text-muted-foreground">Claims matching a known fraud pattern</p>
          </div>
          {Object.entries(data.relationship_findings.by_type).map(([type, count]) => (
            <div key={type}>
              <p className="text-2xl font-bold text-foreground tabular-nums">{count}</p>
              <p className="text-[10px] font-bold uppercase text-muted-foreground">{type.replace(/_/g, " ")}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TriggerFrequencyList({
  title,
  subtitle,
  items,
  emptyText,
}: {
  title: string;
  subtitle: string;
  items: { label: string; count: number }[];
  emptyText: string;
}) {
  const top = items.sort((a, b) => b.count - a.count).slice(0, 8);
  const max = Math.max(1, ...top.map((i) => i.count));

  return (
    <div className="bg-card p-6 rounded-2xl border border-border shadow-sm">
      <h3 className="text-sm font-bold text-foreground mb-1">{title}</h3>
      <p className="text-xs text-muted-foreground mb-4">{subtitle}</p>
      {top.length === 0 ? (
        <p className="text-xs text-muted-foreground italic">{emptyText}</p>
      ) : (
        <div className="space-y-2.5">
          {top.map((item) => (
            <div key={item.label} className="flex items-center gap-3">
              <span className="text-xs text-foreground/80 capitalize w-40 shrink-0 truncate" title={item.label}>{item.label}</span>
              <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                <div className="h-full bg-primary rounded-full" style={{ width: `${(item.count / max) * 100}%` }} />
              </div>
              <span className="text-xs font-bold text-foreground tabular-nums w-6 text-right">{item.count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ClaimsOverTimeChart({ data }: { data: { date: string; count: number }[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.count));
  const total = data.reduce((sum, d) => sum + d.count, 0);

  return (
    <div className="bg-card p-6 rounded-2xl border border-border shadow-sm">
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h3 className="text-sm font-bold text-foreground">Claims Filed</h3>
          <p className="text-xs text-muted-foreground">Last {data.length || 30} days Â· {total} claims</p>
        </div>
      </div>
      {data.length === 0 ? (
        <p className="text-xs text-muted-foreground italic">No claims filed in this window.</p>
      ) : (
        <div className="relative flex items-end gap-[3px] h-24">
          {data.map((d, i) => (
            <div
              key={d.date}
              className="relative flex-1 h-full flex items-end"
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered((h) => (h === i ? null : h))}
            >
              {hovered === i && (
                <div className="absolute bottom-full mb-1.5 left-1/2 -translate-x-1/2 z-10 bg-foreground text-background text-[10px] font-bold px-2 py-1 rounded whitespace-nowrap pointer-events-none">
                  {d.date}: {d.count}
                </div>
              )}
              <div
                className={`w-full rounded-t-sm transition-colors ${hovered === i ? "bg-primary" : "bg-primary/60"}`}
                style={{ height: `${Math.max(4, (d.count / max) * 100)}%` }}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
