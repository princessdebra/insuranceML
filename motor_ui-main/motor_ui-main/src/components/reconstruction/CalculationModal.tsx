// Shows the real formula and the actual input values behind one
// CALCULATED/ESTIMATED figure -- every "step" here is a genuine field off
// the `physics` prop, never a fabricated illustrative number. When an input
// isn't available on this claim (e.g. Pathway 2 has no telemetry-derived
// energy breakdown), the row says so honestly instead of hiding or faking it.

export type CalculationStep = { label: string; value: string };

export type CalculationDetail = {
  title: string;
  formula: string;
  formulaLabel?: string;
  formulaNote?: string;
  steps: CalculationStep[];
  resultLabel: string;
  resultValue: string;
  caveat?: string;
};

export default function CalculationModal({ detail, onClose }: { detail: CalculationDetail; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-card shadow-xl max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border">
          <p className="text-sm font-black text-foreground">{detail.title}</p>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground rounded-full w-7 h-7 flex items-center justify-center hover:bg-muted"
            aria-label="Close"
          >
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        <div className="p-4 space-y-4">
          <div className="rounded-xl bg-muted/40 border border-border p-3">
            <p className="text-[9px] font-black uppercase tracking-widest text-muted-foreground mb-1">{detail.formulaLabel || "Formula"}</p>
            <p className="text-sm font-mono font-semibold text-foreground">{detail.formula}</p>
            {detail.formulaNote && <p className="text-[10px] text-muted-foreground mt-1.5 leading-relaxed">{detail.formulaNote}</p>}
          </div>

          {detail.steps.length > 0 && (
            <div>
              <p className="text-[9px] font-black uppercase tracking-widest text-muted-foreground mb-2">Inputs used for this claim</p>
              <div className="space-y-1.5">
                {detail.steps.map((s, i) => (
                  <div key={i} className="flex items-center justify-between gap-3 text-xs py-1 border-b border-border/60 last:border-0">
                    <span className="text-muted-foreground">{s.label}</span>
                    <span className="font-bold text-foreground tabular-nums text-right">{s.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-xl bg-primary/5 border border-primary/20 p-3 flex items-center justify-between">
            <span className="text-xs font-bold text-foreground">{detail.resultLabel}</span>
            <span className="text-base font-black text-primary tabular-nums">{detail.resultValue}</span>
          </div>

          {detail.caveat && (
            <p className="text-[10px] text-muted-foreground italic leading-relaxed">{detail.caveat}</p>
          )}
        </div>
      </div>
    </div>
  );
}
