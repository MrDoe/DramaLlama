import type { CSSProperties } from "react";

const SPEAKER_COLORS = [
  "#fb923c",
  "#a78bfa",
  "#2dd4bf",
  "#f472b6",
  "#60a5fa",
  "#facc15",
  "#4ade80",
  "#f87171",
];

export function speakerColor(name: string): string {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) >>> 0;
  }
  return SPEAKER_COLORS[hash % SPEAKER_COLORS.length];
}

export function speakerStyle(name: string): CSSProperties {
  return { "--speaker-color": speakerColor(name) } as CSSProperties;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 1) return "0:00";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function formatDate(value: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return "";
  }
}

export function waveBars(seed: string, count = 30): number[] {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 33 + seed.charCodeAt(index)) >>> 0;
  }
  return Array.from({ length: count }, (_, index) => {
    hash = (hash * 1664525 + 1013904223) >>> 0;
    const value = (hash % 100) / 100;
    const envelope = Math.sin((index / count) * Math.PI) * 0.45 + 0.55;
    return Math.round((18 + value * 82) * envelope);
  });
}

export function classNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}
