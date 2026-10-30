import React, { useCallback, useEffect, useRef, useState } from "react";

export interface Cut {
  line_id: string;
  start_s: number;
  end_s: number;
}

interface Props {
  audioUrl: string;
  cuts: Cut[];
  onChange: (cuts: Cut[]) => void;
  height?: number;
}

const COLORS = [
  "rgba(99, 102, 241, 0.25)",
  "rgba(16, 185, 129, 0.25)",
  "rgba(245, 158, 11, 0.25)",
  "rgba(236, 72, 153, 0.25)",
];

export function WaveformCutter({ audioUrl, cuts, onChange, height = 140 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [duration, setDuration] = useState(0);
  const [localCuts, setLocalCuts] = useState<Cut[]>(cuts);
  const [dragging, setDragging] = useState<{ idx: number; edge: "start" | "end" } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setLocalCuts(cuts), [cuts]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(audioUrl);
        const arr = await res.arrayBuffer();
        const ctx = audioCtxRef.current ?? new AudioContext();
        audioCtxRef.current = ctx;
        const buf = await ctx.decodeAudioData(arr.slice(0));
        if (cancelled) return;
        setDuration(buf.duration);
        // downsample peaks
        const ch = buf.getChannelData(0);
        const samplesPerPixel = Math.max(1, Math.floor(ch.length / 1200));
        const p: number[] = [];
        for (let i = 0; i < ch.length; i += samplesPerPixel) {
          let max = 0;
          for (let j = 0; j < samplesPerPixel && i + j < ch.length; j++) {
            const v = Math.abs(ch[i + j]);
            if (v > max) max = v;
          }
          p.push(max);
        }
        setPeaks(p);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "decode failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [audioUrl]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const dpr = window.devicePixelRatio || 1;
    const w = container.clientWidth;
    const h = height;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.fillStyle = "#0f0f1a";
    ctx.fillRect(0, 0, w, h);

    if (peaks.length === 0 || duration <= 0) return;

    const mid = h / 2;
    const pxPerSec = w / duration;

    // waveform
    ctx.strokeStyle = "rgba(148, 163, 184, 0.7)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    const step = w / peaks.length;
    for (let i = 0; i < peaks.length; i++) {
      const x = i * step;
      const amp = peaks[i] * (h * 0.45);
      ctx.moveTo(x, mid - amp);
      ctx.lineTo(x, mid + amp);
    }
    ctx.stroke();

    // regions
    localCuts.forEach((cut, idx) => {
      const x0 = cut.start_s * pxPerSec;
      const x1 = cut.end_s * pxPerSec;
      ctx.fillStyle = COLORS[idx % COLORS.length];
      ctx.fillRect(x0, 0, Math.max(1, x1 - x0), h);
      // boundary lines
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x0, 0);
      ctx.lineTo(x0, h);
      ctx.moveTo(x1, 0);
      ctx.lineTo(x1, h);
      ctx.stroke();
      // label
      ctx.fillStyle = "#fff";
      ctx.font = "11px sans-serif";
      ctx.fillText(`${idx + 1} ${cut.line_id}`, x0 + 4, 12);
    });

    // time ticks
    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.font = "9px sans-serif";
    for (let t = 0; t <= duration; t += Math.max(1, Math.round(duration / 8))) {
      const x = t * pxPerSec;
      ctx.fillText(`${t.toFixed(0)}s`, x + 2, h - 4);
    }
  }, [peaks, duration, localCuts, height]);

  useEffect(() => {
    draw();
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [draw]);

  const findEdge = useCallback(
    (x: number) => {
      const pxPerSec = (containerRef.current?.clientWidth ?? 1) / duration;
      const threshold = 6; // px
      for (let i = 0; i < localCuts.length; i++) {
        const cut = localCuts[i];
        const xs = cut.start_s * pxPerSec;
        const xe = cut.end_s * pxPerSec;
        if (Math.abs(x - xs) <= threshold) return { idx: i, edge: "start" as const };
        if (Math.abs(x - xe) <= threshold) return { idx: i, edge: "end" as const };
      }
      return null;
    },
    [localCuts, duration],
  );

  const onMouseDown = (e: React.MouseEvent) => {
    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    const x = e.clientX - rect.left;
    const hit = findEdge(x);
    if (hit) setDragging(hit);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragging) return;
    const rect = (e.target as HTMLCanvasElement).getBoundingClientRect();
    const x = e.clientX - rect.left;
    const w = rect.width;
    const pxPerSec = w / duration;
    const t = Math.max(0, Math.min(duration, x / pxPerSec));
    setLocalCuts((prev) => {
      const next = prev.map((c) => ({ ...c }));
      const cut = next[dragging.idx];
      if (dragging.edge === "start") {
        const maxStart = cut.end_s - 0.05;
        const minStart = dragging.idx > 0 ? next[dragging.idx - 1].end_s : 0;
        cut.start_s = Math.max(minStart, Math.min(maxStart, t));
      } else {
        const minEnd = cut.start_s + 0.05;
        const maxEnd = dragging.idx < next.length - 1 ? next[dragging.idx + 1].start_s : duration;
        cut.end_s = Math.max(minEnd, Math.min(maxEnd, t));
      }
      return next;
    });
  };

  const onMouseUp = () => {
    if (dragging) {
      setDragging(null);
      onChange(localCuts);
    }
  };

  if (error) {
    return <div className="render-error">waveform: {error}</div>;
  }

  return (
    <div ref={containerRef} style={{ width: "100%" }}>
      <canvas
        ref={canvasRef}
        style={{ cursor: dragging ? "grabbing" : "ew-resize", borderRadius: 6, display: "block" }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      />
      <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
        {localCuts.map((cut, idx) => (
          <span key={cut.line_id} style={{ fontSize: 11, opacity: 0.8 }}>
            {idx + 1}: {cut.start_s.toFixed(2)}–{cut.end_s.toFixed(2)}s
          </span>
        ))}
      </div>
    </div>
  );
}
