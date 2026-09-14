import { useState } from "react";
import { SimEvent } from "./types";

export default function ReconstructionTimeline({
  tStart, tEnd, tSim, events, playing, speed,
  onPlay, onPause, onSeek, onStep, onSpeedChange, onImpactReplay, onReplay,
}: {
  tStart: number; tEnd: number; tSim: number; events: SimEvent[]; playing: boolean; speed: number;
  onPlay: () => void; onPause: () => void; onSeek: (t: number) => void; onStep: (deltaSec: number) => void;
  onSpeedChange: (s: number) => void; onImpactReplay: () => void; onReplay: () => void;
}) {
  const [openEvent, setOpenEvent] = useState<SimEvent | null>(null);
  const pct = (t: number) => ((t - tStart) / Math.max(1e-6, tEnd - tStart)) * 100;

  return (
    <div className="rounded-2xl border border-border bg-card shadow-sm p-4 space-y-3">
      {/* Scrub bar with event markers */}
      <div className="relative h-8">
        <div className="absolute top-1/2 -translate-y-1/2 left-0 right-0 h-1.5 bg-muted rounded-full overflow-hidden">
          <div className="h-full bg-primary/70" style={{ width: `${pct(tSim)}%` }} />
        </div>
        <input
          type="range"
          min={tStart}
          max={tEnd}
          step={0.01}
          value={tSim}
          onChange={(e) => onSeek(Number(e.target.value))}
          className="absolute inset-0 w-full h-8 opacity-0 cursor-pointer"
        />
        <div className="absolute top-1/2 -translate-y-1/2 size-3 rounded-full bg-primary border-2 border-card shadow pointer-events-none" style={{ left: `calc(${pct(tSim)}% - 6px)` }} />
        {events.map((ev) => (
          <button
            key={ev.key}
            onClick={() => { onSeek(ev.t); setOpenEvent(ev); }}
            title={ev.label}
            className={`absolute top-1/2 -translate-y-1/2 size-2.5 rounded-full border border-card ${ev.key === "collision" ? "bg-destructive" : ev.key.startsWith("brake") ? "bg-amber-500" : "bg-foreground/40"}`}
            style={{ left: `calc(${pct(ev.t)}% - 5px)` }}
          />
        ))}
      </div>

      {openEvent && (
        <div className="rounded-xl border border-border bg-muted/30 p-3 text-xs flex items-start justify-between gap-3">
          <div>
            <p className="font-bold text-foreground uppercase tracking-wide text-[10px]">{openEvent.label}</p>
            <p className="text-muted-foreground mt-0.5">T = {openEvent.t >= 0 ? "+" : ""}{openEvent.t.toFixed(2)}s{openEvent.vehicle && openEvent.vehicle !== "both" ? ` · ${openEvent.vehicle}` : ""}</p>
            {openEvent.detail && <p className="text-foreground/80 mt-0.5">{openEvent.detail}</p>}
            <p className="text-muted-foreground/70 mt-0.5">Source: Telemetry</p>
          </div>
          <button onClick={() => setOpenEvent(null)} className="text-muted-foreground hover:text-foreground shrink-0">
            <span className="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
      )}

      {/* Playback controls */}
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => onStep(-0.1)} className="size-8 flex items-center justify-center rounded-full border border-border hover:bg-muted transition-colors" title="Step back">
          <span className="material-symbols-outlined text-[18px]">skip_previous</span>
        </button>
        {playing ? (
          <button onClick={onPause} className="size-9 flex items-center justify-center rounded-full bg-primary text-primary-foreground hover:bg-primary/90 transition-colors" title="Pause">
            <span className="material-symbols-outlined text-[20px]">pause</span>
          </button>
        ) : (
          <button onClick={onPlay} className="size-9 flex items-center justify-center rounded-full bg-primary text-primary-foreground hover:bg-primary/90 transition-colors" title="Play">
            <span className="material-symbols-outlined text-[20px]">play_arrow</span>
          </button>
        )}
        <button onClick={() => onStep(0.1)} className="size-8 flex items-center justify-center rounded-full border border-border hover:bg-muted transition-colors" title="Step forward">
          <span className="material-symbols-outlined text-[18px]">skip_next</span>
        </button>
        <button onClick={onReplay} className="size-8 flex items-center justify-center rounded-full border border-border hover:bg-muted transition-colors" title="Replay">
          <span className="material-symbols-outlined text-[18px]">replay</span>
        </button>

        <span className="h-4 w-px bg-border mx-1" />

        {[0.25, 0.5, 1, 2, 4].map((s) => (
          <button
            key={s}
            onClick={() => onSpeedChange(s)}
            className={`px-2.5 py-1 rounded-full text-[10px] font-bold transition-colors ${speed === s ? "bg-primary text-primary-foreground" : "border border-border text-muted-foreground hover:bg-muted"}`}
          >
            {s}×
          </button>
        ))}

        <span className="h-4 w-px bg-border mx-1" />

        <button
          onClick={onImpactReplay}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold bg-destructive/10 text-destructive hover:bg-destructive/15 transition-colors"
        >
          <span className="material-symbols-outlined text-[14px]">bolt</span>
          Impact Replay
        </button>

        <span className="ml-auto text-xs font-mono text-muted-foreground">
          T = {tSim >= 0 ? "+" : ""}{tSim.toFixed(2)}s
        </span>
      </div>
    </div>
  );
}
