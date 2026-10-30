import { useEffect, useState } from 'react';

// Farbschema: System, Hell oder Dunkel. Das Skript im Kopf von index.html setzt data-theme schon vor dem ersten Bild
// aus localStorage.theme; hier wird nur umgeschaltet. System heißt: kein data-theme, es gilt prefers-color-scheme.

export type ThemeChoice = 'system' | 'light' | 'dark';

const dark = matchMedia('(prefers-color-scheme: dark)');

function stored(): ThemeChoice {
  try {
    const value = localStorage.getItem('theme');
    if (value === 'light' || value === 'dark') return value;
  } catch { /* Speicher gesperrt: die Wahl gilt nur bis zum Neuladen */ }
  return 'system';
}

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void, 'light' | 'dark'] {
  const [choice, setChoice] = useState<ThemeChoice>(stored);
  const [systemDark, setSystemDark] = useState(dark.matches);

  useEffect(() => {
    const changed = () => setSystemDark(dark.matches);
    dark.addEventListener('change', changed);
    return () => dark.removeEventListener('change', changed);
  }, []);

  function choose(next: ThemeChoice) {
    const root = document.documentElement;
    if (next === 'system') delete root.dataset.theme;
    else root.dataset.theme = next;
    try {
      if (next === 'system') localStorage.removeItem('theme');
      else localStorage.setItem('theme', next);
    } catch { /* siehe oben */ }
    setChoice(next);
  }

  return [choice, choose, choice === 'system' ? (systemDark ? 'dark' : 'light') : choice];
}
