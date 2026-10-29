import { useEffect, useRef, useState } from 'react';
import { RigStage, type Clip, type Facts } from './stage';

const clips: [Clip, string][] = [['walk', 'Gehen'], ['punch', 'Schlag'], ['arms', 'Arme heben'], ['squat', 'Kniebeuge']];

// Ein GLB mit Skelett ansehen und bewegen: Proben, Knochen von Hand, Skelett und Drahtgitter.
export function RigViewer({ url }: { url: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const stage = useRef<RigStage | null>(null);
  const [facts, setFacts] = useState<Facts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clip, setClip] = useState<Clip | null>(null);
  const [amp, setAmp] = useState(1);
  const [bone, setBone] = useState(0);
  const [angles, setAngles] = useState<[number, number, number]>([0, 0, 0]);
  const [skeleton, setSkeleton] = useState(true);
  const [wire, setWire] = useState(false);

  useEffect(() => {
    if (!canvas.current) return;
    const color = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#0f766e';
    const s = new RigStage(canvas.current, color);
    stage.current = s;
    setFacts(null); setError(null); setClip(null);
    s.load(url).then(f => {
      setFacts(f);
      const arm = s.bones.indexOf(s.find('LeftArm')!);
      setBone(arm >= 0 ? arm : 0);
    }, e => setError(`Das Modell ließ sich nicht laden: ${(e as Error).message}`));
    return () => s.dispose();
  }, [url]);

  useEffect(() => { if (stage.current) stage.current.amplitude = amp; }, [amp]);
  useEffect(() => { stage.current?.showSkeleton(skeleton); }, [skeleton, facts]);
  useEffect(() => { stage.current?.wireframe(wire); }, [wire, facts]);

  function play(next: Clip) {
    const value = clip === next ? null : next;
    setClip(value);
    setAngles([0, 0, 0]);
    stage.current?.setClip(value);
  }

  function turn(axis: number, value: number) {
    const next = [...angles] as [number, number, number];
    next[axis] = value;
    setAngles(next);
    setClip(null);
    stage.current?.rotate(bone, next);
  }

  function reset() {
    setClip(null); setAngles([0, 0, 0]);
    stage.current?.setClip(null);
  }

  const missing = facts?.core.filter(c => !c.found) ?? [];
  return (
    <div className="rig">
      <div className="stage rig-stage">
        <canvas ref={canvas} />
        {!facts && !error && <div className="empty muted">Lädt das Modell mit Skelett …</div>}
        {error && <div className="empty bad">{error}</div>}
      </div>
      {facts && (
        <div className="rig-controls">
          <div className="row tight">
            {clips.map(([id, label]) => (
              <button key={id} type="button" className={clip === id ? 'primary' : ''} aria-pressed={clip === id} onClick={() => play(id)}>{label}</button>
            ))}
            <label className="small muted amp">Stärke
              <input id="rig-amp" type="range" min={0} max={1.5} step={0.05} value={amp} onChange={e => setAmp(Number(e.target.value))} />
            </label>
          </div>
          <details>
            <summary>Knochen von Hand drehen</summary>
            <div className="bone-grid">
              <select id="rig-bone" value={bone} onChange={e => { setBone(Number(e.target.value)); setAngles([0, 0, 0]); }} aria-label="Knochen">
                {stage.current?.bones.map((b, i) => <option key={i} value={i}>{b.name || `Knochen ${i}`}</option>)}
              </select>
              {(['X', 'Y', 'Z'] as const).map((axis, i) => (
                <label key={axis} className="axis">{axis}
                  <input id={`rig-${axis}`} type="range" min={-120} max={120} value={angles[i]} onChange={e => turn(i, Number(e.target.value))} />
                  <output>{angles[i]}°</output>
                </label>
              ))}
            </div>
          </details>
          <div className="row tight">
            <button type="button" onClick={reset}>Grundhaltung</button>
            <button type="button" className={skeleton ? 'primary' : ''} aria-pressed={skeleton} onClick={() => setSkeleton(!skeleton)}>Skelett</button>
            <button type="button" className={wire ? 'primary' : ''} aria-pressed={wire} onClick={() => setWire(!wire)}>Drahtgitter</button>
            <span className="small muted">
              {facts.bones} Knochen · {facts.vertices.toLocaleString('de-DE')} Punkte · Mixamo-Kern {facts.core.length - missing.length}/{facts.core.length}
            </span>
          </div>
          {missing.length > 0 && <div className="small warn">Ohne Mixamo-Namen: {missing.map(m => m.name).join(', ')}. Proben bewegen diese Teile nicht.</div>}
        </div>
      )}
    </div>
  );
}
