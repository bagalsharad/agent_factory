// Theme preference: "system" follows the OS; "light"/"dark" are explicit choices remembered per browser.
export type ThemePreference = "system" | "light" | "dark";

const KEY = "af.theme";
const media = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function getThemePreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(preference: ThemePreference = getThemePreference()): void {
  const dark = preference === "dark" || (preference === "system" && !!media?.matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function setThemePreference(preference: ThemePreference): void {
  try {
    if (preference === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, preference);
  } catch {
    // storage unavailable (private mode): the choice still applies to this page
  }
  applyTheme(preference);
}

export function watchSystemTheme(): void {
  media?.addEventListener("change", () => applyTheme());
}
