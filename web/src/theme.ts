export type Theme = "classic" | "violet";

export const THEMES: { value: Theme; label: string; description: string }[] = [
  {
    value: "classic",
    label: "Classic",
    description: "The original warm orange accent on a deep navy background.",
  },
  {
    value: "violet",
    label: "Violet",
    description: "The newer violet accent on a darker background.",
  },
];

const STORAGE_KEY = "drama-llama-theme";

export function getTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "classic" || stored === "violet") return stored;
  } catch {
    // localStorage may be unavailable (private mode); fall back to default.
  }
  return "classic";
}

export function applyTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Ignore storage failures; the in-memory apply still works.
  }
  document.documentElement.dataset.theme = theme;
}