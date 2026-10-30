import type { ReactNode } from 'react';
import { useLang, type Lang } from '../i18n';
import type { ThemeChoice } from '../theme';

const icons: Record<ThemeChoice, ReactNode> = {
  system: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></>,
  light: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  dark: <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />,
};

type Props = { theme: ThemeChoice; onTheme: (choice: ThemeChoice) => void };

// Sprache und Farbschema, oben rechts.
export function Settings({ theme, onTheme }: Props) {
  const { lang, t, setLang } = useLang();
  return (
    <div className="settings">
      <div className="segmented" role="group" aria-label={t.settings.language}>
        {(['de', 'en'] as Lang[]).map(l => (
          <button key={l} type="button" lang={l} aria-pressed={lang === l} onClick={() => setLang(l)}>{l.toUpperCase()}</button>
        ))}
      </div>
      <div className="segmented" role="group" aria-label={t.settings.theme}>
        {(['system', 'light', 'dark'] as ThemeChoice[]).map(choice => (
          <button key={choice} type="button" aria-pressed={theme === choice} title={t.settings[choice]} aria-label={t.settings[choice]}
            onClick={() => onTheme(choice)}>
            <svg viewBox="0 0 24 24" aria-hidden="true">{icons[choice]}</svg>
          </button>
        ))}
      </div>
    </div>
  );
}
