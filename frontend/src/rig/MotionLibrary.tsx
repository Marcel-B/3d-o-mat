import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type MotionClip, type MotionIndex } from '../api';
import { useLang } from '../i18n';
import { DEFAULT_OPTIONS, Motion, type MotionOptions } from './retarget';
import type { MotionTake, RigStage } from './stage';

// Längstens so viel einer Aufnahme geht als eine Animation ins GLB (animate.py: höchstens 1000 Bilder, 30 je Sekunde).
const LONGEST = 33;
const MAX_TAKES = 12;
const SHOWN = 150;

type Take = { clip: MotionClip; options: MotionOptions };

// Die Bewegungsbibliothek im Viewer: suchen, auf der eigenen Figur ansehen, einstellen, sammeln und als GLB laden.
// stopKey ändert sich, wenn der Viewer etwas anderes abspielt (Probe, Haltung); dann ist hier nichts mehr ausgewählt.
export function MotionLibrary({ stage, jobId, stopKey, onPlay }: {
  stage: () => RigStage | null; jobId: string; stopKey: number; onPlay: () => void;
}) {
  const { lang, t: { motions: t, rig } } = useLang();
  const [index, setIndex] = useState<MotionIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState('');
  const [category, setCategory] = useState('');
  const [style, setStyle] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<MotionClip | null>(null);
  const [options, setOptions] = useState<MotionOptions>(DEFAULT_OPTIONS);
  const [rootMotion, setRootMotion] = useState(false);
  const [loadingClip, setLoadingClip] = useState(false);
  const [takes, setTakes] = useState<Take[]>([]);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  // Geladene Aufnahmen, damit Vorschau und Export sie nicht jedes Mal neu holen und einlesen.
  const cache = useRef(new Map<string, Motion>());

  useEffect(() => {
    api.motions().then(setIndex, e => setError(t.failed((e as Error).message)));
    // Nur einmal; die Sprache ändert die Fehlermeldung nicht nachträglich.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { setSelected(null); }, [stopKey]);

  const name = (clip: MotionClip) => (lang === 'de' ? clip.de : clip.en);
  const styleName = (clip: MotionClip) => (lang === 'de' ? clip.styleDe : clip.styleEn) ?? clip.style ?? '';
  const styles = useMemo(() => {
    const all = new Map<string, string>();
    index?.clips.forEach(c => { if (c.style) all.set(c.style, styleName(c)); });
    return [...all.entries()];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, lang]);

  const found = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return (index?.clips ?? []).filter(c =>
      (!source || c.source === source) && (!category || c.category === category) && (!style || c.style === style) &&
      words.every(w => `${c.de} ${c.en} ${c.styleDe ?? ''} ${c.styleEn ?? ''} ${c.ref}`.toLowerCase().includes(w)));
  }, [index, source, category, style, query]);

  async function motionOf(clip: MotionClip) {
    let motion = cache.current.get(clip.id);
    if (!motion) {
      motion = new Motion(await api.motionBvh(clip.id), clip.source);
      cache.current.set(clip.id, motion);
    }
    return motion;
  }

  async function show(clip: MotionClip, next: MotionOptions) {
    setLoadingClip(true); setError(null);
    try {
      const motion = await motionOf(clip);
      stage()?.setMotion(motion, next);
    } catch (e) {
      setError(t.clipFailed((e as Error).message));
    } finally {
      setLoadingClip(false);
    }
  }

  function pick(clip: MotionClip) {
    if (selected?.id === clip.id) {
      setSelected(null);
      stage()?.setMotion(null);
      return;
    }
    const next = { ...options, start: 0, end: Math.min(clip.seconds, LONGEST) };
    setSelected(clip); setOptions(next); onPlay();
    show(clip, next);
  }

  function change(patch: Partial<MotionOptions>) {
    const next = { ...options, ...patch };
    // Anfang und Ende: mindestens eine Zehntelsekunde dazwischen, höchstens LONGEST.
    if (patch.start !== undefined) next.end = Math.min(Math.max(next.end, next.start + 0.1), next.start + LONGEST);
    if (patch.end !== undefined) next.start = Math.max(Math.min(next.start, next.end - 0.1), next.end - LONGEST, 0);
    setOptions(next);
    if (selected) show(selected, next);
  }

  function add() {
    if (selected && takes.length < MAX_TAKES) setTakes([...takes, { clip: selected, options }]);
  }

  // Der Name im GLB: Aufnahme, Stil, gespiegelt; doppelte bekommen eine Nummer.
  function names() {
    const used = new Map<string, number>();
    return takes.map(({ clip, options: o }) => {
      let label = name(clip) + (clip.style && clip.style !== 'normal' ? ` (${styleName(clip)})` : '');
      if (o.mirror) label += lang === 'de' ? ' gespiegelt' : ' mirrored';
      label = label.slice(0, 54);
      const n = (used.get(label) ?? 0) + 1;
      used.set(label, n);
      return n > 1 ? `${label} ${n}` : label;
    });
  }

  async function download() {
    const s = stage();
    if (!s || !index || !takes.length) return;
    setExporting(true); setExportError(null);
    try {
      const labels = names();
      const list: MotionTake[] = [];
      for (const [i, { clip, options: o }] of takes.entries()) {
        const src = index.sources[clip.source];
        list.push({
          name: labels[i], motion: await motionOf(clip), options: o,
          extras: { source: src?.name ?? clip.source, recording: clip.ref, license: src?.licenseEn ?? '', credit: src?.credit ?? '' },
        });
      }
      const { blob, name: file } = await api.animatedGlb(jobId, s.bakeMotions(list, { rootMotion }));
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = file;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    } catch (e) {
      setExportError(rig.exportFailed((e as Error).message));
    } finally {
      setExporting(false);
    }
  }

  if (error && !index) return <div className="error">{error}</div>;
  if (!index) return <div className="small muted">{t.loading}</div>;
  if (!index.clips.length) return <div className="small muted">{t.empty}</div>;
  const nonCommercial = (clip: MotionClip) => index.sources[clip.source]?.commercial === false;
  const usedSources = [...new Set(takes.map(k => k.clip.source))].map(k => index.sources[k]?.name ?? k);
  const seconds = (v: number) => t.seconds(v.toLocaleString(lang === 'de' ? 'de-DE' : 'en-GB', { maximumFractionDigits: 1 }));
  return (
    <div className="motions">
      <div className="row tight">
        <select id="motion-source" value={source} onChange={e => setSource(e.target.value)} aria-label={t.allSources}>
          <option value="">{t.allSources}</option>
          {Object.entries(index.sources).map(([id, s]) => (
            <option key={id} value={id}>{s.name.replace(' Graphics Lab Motion Capture Database', '').replace(' Research Motiondataset', '')}{s.commercial ? '' : ` (${t.nonCommercial})`}</option>
          ))}
        </select>
        <select id="motion-category" value={category} onChange={e => setCategory(e.target.value)} aria-label={t.allCategories}>
          <option value="">{t.allCategories}</option>
          {Object.entries(index.categories).map(([id, c]) => <option key={id} value={id}>{lang === 'de' ? c.de : c.en}</option>)}
        </select>
        {styles.length > 0 && source !== 'cmu' && (
          <select id="motion-style" value={style} onChange={e => setStyle(e.target.value)} aria-label={t.allStyles}>
            <option value="">{t.allStyles}</option>
            {styles.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        )}
        <input id="motion-search" className="grow" value={query} placeholder={t.search} aria-label={t.search}
          onChange={e => setQuery(e.target.value)} />
        <span className="small muted">{t.count(found.length, index.clips.length)}</span>
      </div>
      <div className="motion-list" role="listbox" aria-label={t.title}>
        {found.slice(0, SHOWN).map(clip => (
          <button key={clip.id} type="button" role="option" aria-selected={selected?.id === clip.id}
            className={selected?.id === clip.id ? 'primary' : ''} onClick={() => pick(clip)}>
            <span>{name(clip)}{clip.style && clip.style !== 'normal' ? <span className="muted"> · {styleName(clip)}</span> : null}</span>
            <span className="small muted">
              {nonCommercial(clip) && <span className="badge" title={index.sources[clip.source]?.[lang === 'de' ? 'license' : 'licenseEn']}>NC</span>}
              {seconds(clip.seconds)}
            </span>
          </button>
        ))}
      </div>
      {selected && (
        <div className="motion-options">
          <label className="small muted amp">{t.speed}
            <input id="motion-speed" type="range" min={0.25} max={2} step={0.05} value={options.speed} onChange={e => change({ speed: Number(e.target.value) })} />
            <output>{options.speed.toFixed(2)}×</output>
          </label>
          <label className="small muted amp">{t.start}
            <input id="motion-start" type="range" min={0} max={selected.seconds} step={0.1} value={options.start} onChange={e => change({ start: Number(e.target.value) })} />
            <output>{seconds(options.start)}</output>
          </label>
          <label className="small muted amp">{t.end}
            <input id="motion-end" type="range" min={0} max={selected.seconds} step={0.1} value={options.end} onChange={e => change({ end: Number(e.target.value) })} />
            <output>{seconds(options.end)}</output>
          </label>
          <label className="small check"><input id="motion-in-place" type="checkbox" checked={options.inPlace} onChange={e => change({ inPlace: e.target.checked })} /> {t.inPlace}</label>
          <label className="small check"><input id="motion-mirror" type="checkbox" checked={options.mirror} onChange={e => change({ mirror: e.target.checked })} /> {t.mirror}</label>
          <label className="small check" title={t.footLockHint}><input id="motion-feet" type="checkbox" checked={options.footLock} disabled={options.inPlace} onChange={e => change({ footLock: e.target.checked })} /> {t.footLock}</label>
          <label className="small check" title={t.loopHint}><input id="motion-loop" type="checkbox" checked={options.loop} onChange={e => change({ loop: e.target.checked })} /> {t.loop}</label>
          {stage()?.hasFingers && (
            <label className="small muted amp" title={t.fistHint}>{t.fist}
              <input id="motion-fist" type="range" min={0} max={100} step={5} value={Math.round(options.fist * 100)} onChange={e => change({ fist: Number(e.target.value) / 100 })} />
              <output>{Math.round(options.fist * 100)} %</output>
            </label>
          )}
          <label className="small muted amp" title={t.armSpaceHint}>{t.armSpace}
            <input id="motion-arms" type="range" min={-15} max={30} step={1} value={options.armSpace} onChange={e => change({ armSpace: Number(e.target.value) })} />
            <output>{options.armSpace > 0 ? '+' : ''}{options.armSpace}°</output>
          </label>
          <button type="button" onClick={add} disabled={loadingClip || takes.length >= MAX_TAKES}>{t.add}</button>
          {takes.length >= MAX_TAKES && <span className="small muted">{t.full}</span>}
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {takes.length > 0 && (
        <div className="motion-takes">
          <div className="small muted">{t.selection}</div>
          <ul>
            {takes.map((take, i) => (
              <li key={i}>
                <span>{names()[i]}</span>
                <span className="small muted">
                  {seconds((take.options.end - take.options.start) / take.options.speed)}
                  {take.options.speed !== 1 && ` · ${take.options.speed.toFixed(2)}×`}
                  {take.options.inPlace && ` · ${t.inPlace}`}
                  {take.options.loop && ` · ${t.loop}`}
                  {take.options.fist > 0 && ` · ${t.fist}`}
                  {nonCommercial(take.clip) && ' · NC'}
                </span>
                <button type="button" className="link" onClick={() => setTakes(takes.filter((_, j) => j !== i))}>{t.remove}</button>
              </li>
            ))}
          </ul>
          <div className="row tight">
            {stage()?.canRootMotion && (
              <label className="small check" title={t.rootMotionHint}><input id="motion-root" type="checkbox" checked={rootMotion} onChange={e => setRootMotion(e.target.checked)} /> {t.rootMotion}</label>
            )}
            <button type="button" className="primary" onClick={download} disabled={exporting}>{exporting ? t.packing : t.download(takes.length)}</button>
          </div>
          {takes.some(k => nonCommercial(k.clip)) && <div className="small warn">{t.ncWarning}</div>}
          <div className="small muted">{t.credit(usedSources.join(', '))}</div>
          {exportError && <div className="error">{exportError}</div>}
        </div>
      )}
      <div className="small muted">{t.hint}</div>
    </div>
  );
}
