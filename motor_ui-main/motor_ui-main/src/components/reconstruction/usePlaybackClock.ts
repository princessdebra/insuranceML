import { useCallback, useEffect, useRef, useState } from "react";

/** Drives `tSim` (simulation time, seconds, 0 = impact) forward at real
 * telemetry pace scaled by `speed`, via requestAnimationFrame -- no fixed
 * step, so playback stays smooth regardless of monitor refresh rate. */
export function usePlaybackClock(tStart: number, tEnd: number) {
  const [tSim, setTSim] = useState(tStart);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const rafRef = useRef<number | null>(null);
  const lastFrameRef = useRef<number | null>(null);
  const tSimRef = useRef(tSim);
  tSimRef.current = tSim;

  const stopLoop = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    lastFrameRef.current = null;
  }, []);

  useEffect(() => {
    if (!playing) { stopLoop(); return; }
    const tick = (now: number) => {
      if (lastFrameRef.current == null) lastFrameRef.current = now;
      const dtMs = now - lastFrameRef.current;
      lastFrameRef.current = now;
      const next = tSimRef.current + (dtMs / 1000) * speed;
      if (next >= tEnd) {
        setTSim(tEnd);
        setPlaying(false);
        return;
      }
      setTSim(next);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return stopLoop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed, tEnd]);

  const play = useCallback(() => {
    if (tSimRef.current >= tEnd) setTSim(tStart);
    setPlaying(true);
  }, [tStart, tEnd]);
  const pause = useCallback(() => setPlaying(false), []);
  const replay = useCallback(() => { setTSim(tStart); setPlaying(true); }, [tStart]);
  const seek = useCallback((t: number) => { setPlaying(false); setTSim(Math.max(tStart, Math.min(tEnd, t))); }, [tStart, tEnd]);
  const stepFrame = useCallback((deltaSec: number) => {
    setPlaying(false);
    setTSim((cur) => Math.max(tStart, Math.min(tEnd, cur + deltaSec)));
  }, [tStart, tEnd]);

  /** Jumps to ~1s before impact, slows down, plays through, pauses just
   * after -- the spec's "Impact Replay" button. */
  const impactReplay = useCallback(() => {
    setSpeed(0.25);
    setTSim(Math.max(tStart, -1));
    setPlaying(true);
    const pauseAt = Math.min(tEnd, 0.5);
    const checkInterval = setInterval(() => {
      if (tSimRef.current >= pauseAt) {
        setPlaying(false);
        clearInterval(checkInterval);
      }
    }, 50);
  }, [tStart, tEnd]);

  return { tSim, playing, speed, setSpeed, play, pause, replay, seek, stepFrame, impactReplay };
}
