import { useEffect, useState } from "react";

const STEPS = [
  "Vehicle information",
  "Damage information",
  "Road conditions",
  "Calculating collision dynamics",
  "Generating timeline",
  "Preparing visualization",
];

/** There's no real backend progress stream to hook into for a single
 * already-computed record fetch, so this is a UX-only staged reveal (fixed
 * timing) -- not a claim about live progress on data that doesn't stream. */
export default function LoadingSequence() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (step >= STEPS.length - 1) return;
    const t = setTimeout(() => setStep((s) => s + 1), 260);
    return () => clearTimeout(t);
  }, [step]);

  return (
    <div className="rounded-2xl border border-border bg-card shadow-sm p-8 flex flex-col items-center justify-center min-h-[300px]">
      <p className="text-xs font-black uppercase tracking-widest text-muted-foreground mb-4">Analyzing claim...</p>
      <div className="space-y-2 w-full max-w-xs">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center gap-2 text-xs">
            <span className={`material-symbols-outlined text-[16px] ${i < step ? "text-primary" : i === step ? "text-primary animate-pulse" : "text-muted-foreground/40"}`}>
              {i < step ? "check_circle" : i === step ? "radio_button_checked" : "radio_button_unchecked"}
            </span>
            <span className={i <= step ? "text-foreground font-medium" : "text-muted-foreground"}>{s}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
