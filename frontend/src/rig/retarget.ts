// Eine Aufnahme (BVH aus der Bewegungsbibliothek, omat3d/motions.py) auf das Skelett der Figur übertragen.
// Die Knochen der Aufnahme heißen je nach Sammlung anders; sie werden auf Mixamo-Namen abgebildet. Dann wird für jeden
// Knochen jede Drehung relativ zur Ruhehaltung in den Achsen der Szene übertragen; Arme und Beine werden vorher in
// ihrer Ruhehaltung angeglichen (die Richtung zum Kind in beiden Skeletten, so passt eine T-Pose-Aufnahme auch auf eine
// Figur in A-Pose). Die Hüftbahn wird mit dem Verhältnis der Beinlängen skaliert. Schaut die Figur anders als die Aufnahme, dreht G die Aufnahme um die
// Hochachse auf sie.
import * as THREE from 'three';
import { BVHLoader } from 'three/examples/jsm/loaders/BVHLoader.js';

// Knochennamen der Sammlungen -> Mixamo. CMU (BVH-Fassung von B. Hahne) und Bandai Namco Research Motiondataset.
const NAMES: Record<string, Record<string, string>> = {
  cmu: {
    Hips: 'Hips', LowerBack: 'Spine', Spine: 'Spine1', Spine1: 'Spine2', Neck: 'Neck', Head: 'Head',
    LeftShoulder: 'LeftShoulder', LeftArm: 'LeftArm', LeftForeArm: 'LeftForeArm', LeftHand: 'LeftHand',
    RightShoulder: 'RightShoulder', RightArm: 'RightArm', RightForeArm: 'RightForeArm', RightHand: 'RightHand',
    LeftUpLeg: 'LeftUpLeg', LeftLeg: 'LeftLeg', LeftFoot: 'LeftFoot', LeftToeBase: 'LeftToeBase',
    RightUpLeg: 'RightUpLeg', RightLeg: 'RightLeg', RightFoot: 'RightFoot', RightToeBase: 'RightToeBase',
  },
  bandai: {
    Hips: 'Hips', Spine: 'Spine', Chest: 'Spine2', Neck: 'Neck', Head: 'Head',
    Shoulder_L: 'LeftShoulder', UpperArm_L: 'LeftArm', LowerArm_L: 'LeftForeArm', Hand_L: 'LeftHand',
    Shoulder_R: 'RightShoulder', UpperArm_R: 'RightArm', LowerArm_R: 'RightForeArm', Hand_R: 'RightHand',
    UpperLeg_L: 'LeftUpLeg', LowerLeg_L: 'LeftLeg', Foot_L: 'LeftFoot', Toes_L: 'LeftToeBase',
    UpperLeg_R: 'RightUpLeg', LowerLeg_R: 'RightLeg', Foot_R: 'RightFoot', Toes_R: 'RightToeBase',
  },
};

// Eltern vor Kindern.
const ORDER = ['Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head', 'LeftShoulder', 'LeftArm', 'LeftForeArm', 'LeftHand',
  'RightShoulder', 'RightArm', 'RightForeArm', 'RightHand', 'LeftUpLeg', 'LeftLeg', 'LeftFoot', 'LeftToeBase',
  'RightUpLeg', 'RightLeg', 'RightFoot', 'RightToeBase'];
// Wohin ein Knochen zeigt: zum ersten dieser Kinder, das beide Skelette haben. Nur Arme und Beine werden so an die
// Aufnahme angeglichen (ihre Ruhehaltung ist Konvention: T- oder A-Pose). Rumpf, Hals, Kopf und Füße behalten ihre
// Grundhaltung und bekommen nur die Änderung der Aufnahme: aufrecht stehen, geradeaus schauen und flach auf dem Boden
// stehen beide ohnehin, und wo SkinTokens Hals-, Kopf- und Zehengelenk setzt, sagt nichts über die Haltung. Sonst
// lehnt eine Figur, deren Kopfgelenk vor dem Halsgelenk liegt, in jeder Aufnahme den Kopf zurück und steht auf den
// Hacken, wenn ihr Knöchel höher über den Zehen sitzt als in der Aufnahme.
const CHILD: Record<string, string[]> = {
  LeftShoulder: ['LeftArm'], LeftArm: ['LeftForeArm'], LeftForeArm: ['LeftHand'],
  RightShoulder: ['RightArm'], RightArm: ['RightForeArm'], RightForeArm: ['RightHand'],
  LeftUpLeg: ['LeftLeg'], LeftLeg: ['LeftFoot'],
  RightUpLeg: ['RightLeg'], RightLeg: ['RightFoot'],
};
// Knochen ohne Kind übernehmen den Ausgleich ihres Elternteils, sonst knickt etwa die Hand gegen den Unterarm ab.
const PARENT: Record<string, string> = { LeftHand: 'LeftForeArm', RightHand: 'RightForeArm' };
// Hals und Kopf folgen der Linie vom Hals- zum Kopfgelenk der Aufnahme (wie sie sich gegen die Ruhehaltung neigt).
// Die Drehung der Kopfknochen selbst ist in den CMU-Aufnahmen nicht zu gebrauchen: gegen ihre Nullpose ist sie um gut
// 20 Grad nach hinten gekippt und oft um 20 Grad zur Seite gedreht, obwohl die Linie stillsteht. Ein Blick zur Seite
// geht damit verloren; die Gehaufnahmen haben keinen.
const NECK = ['Neck', 'Head'];
const mirrored = (name: string) => name.replace(/^Left/, '\0').replace(/^Right/, 'Left').replace('\0', 'Right');

// Wie eine Aufnahme abgespielt wird. armSpace: Grad, um die die Arme vom Körper weg (positiv) oder heran gehen;
// footLock: Füße am Boden festhalten; loop: das Ende weich in den Anfang führen.
export type MotionOptions = {
  speed: number; inPlace: boolean; mirror: boolean; start: number; end: number;
  armSpace: number; footLock: boolean; loop: boolean;
  // fist: wie fest beide Hände geschlossen sind (0 offen, 1 Faust); die Aufnahmen bringen kaum Fingerbewegung mit.
  fist: number;
};
export const DEFAULT_OPTIONS: MotionOptions = {
  speed: 1, inPlace: false, mirror: false, start: 0, end: 1, armSpace: 0, footLock: true, loop: false, fist: 0,
};
// Bilder je Sekunde, in denen Bodenkontakte gesucht und Schleifen geglättet werden (wie der Export).
const FPS = 30;
const ARMS = ['LeftArm', 'LeftForeArm', 'LeftHand', 'RightArm', 'RightForeArm', 'RightHand'];
const LEGS = [['LeftUpLeg', 'LeftLeg', 'LeftFoot'], ['RightUpLeg', 'RightLeg', 'RightFoot']] as const;

// Zwei Knochen (Oberschenkel, Schienbein) so drehen, dass das Ende (Knöchel) auf target liegt; das Knie bleibt in
// seiner Ebene und knickt nach vorn (bend), wenn das Bein gestreckt war. Der Fuß behält seine Lage im Raum.
function reach(upper: THREE.Bone, lower: THREE.Bone, end: THREE.Bone, target: THREE.Vector3, bend: THREE.Vector3) {
  const a = worldP(upper), b = worldP(lower), c = worldP(end);
  const footWorld = worldQ(end);
  const lab = b.distanceTo(a), lcb = c.distanceTo(b);
  const lat = THREE.MathUtils.clamp(target.distanceTo(a), 0.01 * (lab + lcb), (lab + lcb) * 0.9995);
  const angle = (u: THREE.Vector3, v: THREE.Vector3) => u.clone().normalize().angleTo(v.clone().normalize());
  const clampCos = (x: number) => Math.acos(THREE.MathUtils.clamp(x, -1, 1));
  const ac = c.clone().sub(a), ab = b.clone().sub(a);
  const acAb = angle(ac, ab), baBc = angle(a.clone().sub(b), c.clone().sub(b));
  const acAb1 = clampCos((lcb * lcb - lab * lab - lat * lat) / (-2 * lab * lat));
  const baBc1 = clampCos((lat * lat - lab * lab - lcb * lcb) / (-2 * lab * lcb));
  let axis = new THREE.Vector3().crossVectors(ac, ab);
  if (axis.lengthSq() < 1e-10) axis = new THREE.Vector3().crossVectors(ab, bend);
  if (axis.lengthSq() < 1e-10) return;
  axis.normalize();
  const turn = (bone: THREE.Bone, rot: THREE.Quaternion) => {
    bone.parent!.updateWorldMatrix(true, false);
    const world = rot.multiply(worldQ(bone));
    bone.quaternion.copy(worldQ(bone.parent!).invert().multiply(world));
    bone.updateMatrixWorld(true);
  };
  turn(upper, new THREE.Quaternion().setFromAxisAngle(axis, acAb1 - acAb));
  turn(lower, new THREE.Quaternion().setFromAxisAngle(axis, baBc1 - baBc));
  const reached = worldP(end).sub(a);
  turn(upper, new THREE.Quaternion().setFromUnitVectors(reached.normalize(), target.clone().sub(a).normalize()));
  end.quaternion.copy(worldQ(lower).invert().multiply(footWorld));
  end.updateMatrixWorld(true);
}

type Rest = { q: Record<string, THREE.Quaternion>; p: Record<string, THREE.Vector3> };

const worldQ = (o: THREE.Object3D) => o.getWorldQuaternion(new THREE.Quaternion());
const worldP = (o: THREE.Object3D) => o.getWorldPosition(new THREE.Vector3());

function measure(bones: Record<string, THREE.Object3D>): Rest {
  const rest: Rest = { q: {}, p: {} };
  for (const [name, bone] of Object.entries(bones)) { rest.q[name] = worldQ(bone); rest.p[name] = worldP(bone); }
  return rest;
}

// Blickrichtung aus der Hüfte: quer zur Linie von rechter zu linker Hüfte, waagrecht.
function facing(p: Record<string, THREE.Vector3>) {
  const side = p.LeftUpLeg.clone().sub(p.RightUpLeg);
  side.y = 0;
  return new THREE.Vector3().crossVectors(side.normalize(), new THREE.Vector3(0, 1, 0)).negate();
}

// Die Aufnahme: ein eigenes Skelett außerhalb der Szene, abgespielt mit einem AnimationMixer. Das erste Bild jeder
// Datei der Bibliothek ist die Ruhehaltung (CMU: T-Pose, Bandai Namco: ruhiges Stehen), die Bewegung beginnt beim zweiten.
export class Motion {
  readonly duration: number;
  readonly fps: number;
  private root: THREE.Bone;
  private mixer: THREE.AnimationMixer;
  bones: Record<string, THREE.Bone> = {};
  rest: Rest;
  hipHeight: number;
  frame0: THREE.Vector3;
  ankle0: number;

  constructor(text: string, source: string) {
    const names = NAMES[source];
    if (!names) throw new Error(`unbekannte Sammlung ${source}`);
    const result = new BVHLoader().parse(text);
    this.root = result.skeleton.bones[0];
    for (const bone of result.skeleton.bones) if (names[bone.name]) this.bones[names[bone.name]] = bone;
    for (const name of ['Hips', 'LeftUpLeg', 'RightUpLeg', 'LeftFoot', 'RightFoot']) {
      if (!this.bones[name]) throw new Error(`Aufnahme ohne ${name}`);
    }
    this.mixer = new THREE.AnimationMixer(this.root);
    this.mixer.clipAction(result.clip).play();
    const track = result.clip.tracks[0];
    this.fps = track && track.times.length > 1 ? 1 / (track.times[1] - track.times[0]) : 30;
    this.duration = Math.max(0, result.clip.duration - 1 / this.fps);
    this.mixer.setTime(0);
    this.root.updateMatrixWorld(true);
    this.rest = measure(this.bones);
    this.hipHeight = this.rest.p.Hips.y - Math.min(this.rest.p.LeftFoot.y, this.rest.p.RightFoot.y);
    this.at(0);
    this.frame0 = worldP(this.bones.Hips);
    this.ankle0 = Math.min(worldP(this.bones.LeftFoot).y, worldP(this.bones.RightFoot).y);
  }

  // Zeit in der Bewegung, ab ihrem ersten Bild (nach der Ruhehaltung).
  at(time: number) {
    this.mixer.setTime(1 / this.fps + Math.max(0, Math.min(this.duration, time)));
    this.root.updateMatrixWorld(true);
  }
}

// Überträgt eine Aufnahme auf eine Figur. bones: Mixamo-Name -> Knochen der Figur, in Grundhaltung gemessen.
export class Retarget {
  private target: Record<string, THREE.Bone> = {};
  private rest: Rest;
  private ankle: number;
  private hipHeight: number;
  private align: Record<string, THREE.Quaternion> = {};
  private G = new THREE.Quaternion();
  private Gi = new THREE.Quaternion();
  // Spiegeln an der Ebene, die die Figur in links und rechts teilt (Normale: ihre Querachse).
  private normal = new THREE.Vector3(1, 0, 0);
  private scale = 1;
  // Vorwärts (aus den Zehen, sonst quer zur Hüfte) für das Knie, und je Seite die Achse, um die ein Arm nach außen geht.
  private ahead = new THREE.Vector3(0, 0, 1);
  private outward: Record<string, THREE.Vector3> = {};
  // Die Grundhaltung der übertragenen Knochen (lokal), um zwischendurch zu ihr zurückzukehren.
  private restLocal: Record<string, [THREE.Quaternion, THREE.Vector3]> = {};
  // Bodenkontakt je Bild und Fuß: wie stark (0 bis 1) und wo der Knöchel festgehalten wird.
  private locks: { weight: number; at: THREE.Vector3 }[][] = [];
  // Root Motion: die waagrechte Bewegung geht auf das Objekt über der Hüfte (Armature) statt auf die Hüfte.
  private holder: THREE.Object3D | null = null;
  private holderRest = new THREE.Vector3();

  constructor(find: (name: string) => THREE.Bone | undefined, private motion: Motion, private options: MotionOptions,
    holder: THREE.Object3D | null = null) {
    for (const name of ORDER) {
      const bone = find(name);
      if (bone) this.target[name] = bone;
    }
    if (!this.target.Hips) throw new Error('Figur ohne Hips');
    this.target.Hips.parent?.updateMatrixWorld(true);
    this.target.Hips.updateMatrixWorld(true);
    this.rest = measure(this.target);
    for (const [name, bone] of Object.entries(this.target)) this.restLocal[name] = [bone.quaternion.clone(), bone.position.clone()];
    if (holder && !options.inPlace && holder === this.target.Hips.parent) {
      this.holder = holder;
      this.holderRest.copy(holder.position);
    }
    const feet = ['LeftFoot', 'RightFoot'].filter(n => this.rest.p[n]).map(n => this.rest.p[n].y);
    this.ankle = feet.length ? Math.min(...feet) : 0;
    this.hipHeight = this.rest.p.Hips.y - this.ankle || 1;
    this.scale = this.hipHeight / (motion.hipHeight || 1);
    const ahead = this.rest.p.LeftUpLeg && this.rest.p.RightUpLeg ? facing(this.rest.p) : new THREE.Vector3(0, 0, 1);
    this.G.setFromUnitVectors(facing(motion.rest.p), ahead);
    this.Gi.copy(this.G).invert();
    this.normal.crossVectors(new THREE.Vector3(0, 1, 0), ahead).normalize();
    const foot = this.rest.p.LeftFoot, toe = this.rest.p.LeftToeBase;
    const forward = foot && toe ? toe.clone().sub(foot).setY(0) : new THREE.Vector3();
    this.ahead.copy(forward.lengthSq() > 1e-8 ? forward.normalize() : ahead);
    for (const side of ['Left', 'Right']) {
      const shoulder = this.rest.p[`${side}Arm`];
      if (!shoulder) continue;
      const out = shoulder.clone().sub(this.rest.p.Hips).setY(0).normalize();
      // Um diese Achse gedreht, geht ein herabhängender Arm (nach unten) zur Seite hin (nach außen).
      this.outward[side] = new THREE.Vector3().crossVectors(new THREE.Vector3(0, -1, 0), out).normalize();
    }
    for (const name of ORDER) {
      if (!this.target[name]) continue;
      const from = this.source(name);
      const child = (CHILD[name] ?? []).find(c => this.target[c] && motion.bones[this.source(c)]);
      if (child && motion.bones[from]) {
        const dT = this.rest.p[child].clone().sub(this.rest.p[name]).normalize();
        const dS = this.direction(motion.rest.p[this.source(child)].clone().sub(motion.rest.p[from]));
        this.align[name] = new THREE.Quaternion().setFromUnitVectors(dT, dS.normalize());
      } else {
        this.align[name] = PARENT[name] && this.align[PARENT[name]] ? this.align[PARENT[name]].clone() : new THREE.Quaternion();
      }
    }
      if (options.footLock && !options.inPlace) this.findContacts();
  }

  // Von welchem Knochen der Aufnahme ein Knochen der Figur seine Bewegung bekommt (gespiegelt: von der anderen Seite).
  private source(name: string) {
    return this.options.mirror ? mirrored(name) : name;
  }

  // Eine Richtung der Aufnahme in die Achsen der Figur (gedreht, beim Spiegeln auch gespiegelt).
  private direction(v: THREE.Vector3) {
    v.applyQuaternion(this.G);
    if (this.options.mirror) v.sub(this.normal.clone().multiplyScalar(2 * v.dot(this.normal)));
    return v;
  }

  // Eine Drehung der Aufnahme ebenso; gespiegelt wird eine Drehung mit M·R·M.
  private rotation(q: THREE.Quaternion) {
    const r = this.G.clone().multiply(q).multiply(this.Gi);
    if (!this.options.mirror) return r;
    const n = this.normal, v = new THREE.Vector3(r.x, r.y, r.z);
    const m = n.clone().multiplyScalar(2 * v.dot(n)).sub(v);
    return new THREE.Quaternion(m.x, m.y, m.z, r.w);
  }

  // Die Länge, wie sie mit Tempo und Anfang/Ende abgespielt wird.
  get length() {
    return Math.max(0.1, (this.options.end - this.options.start) / this.options.speed);
  }

  // Die Figur so stellen, wie die Aufnahme sie zur Zeit t (Sekunden der Wiedergabe) zeigt. Die Figur muss vorher in
  // ihrer Grundhaltung stehen.
  apply(t: number) {
    this.pose(t);
    const blend = Math.min(0.4, this.length / 4);
    if (!this.options.loop || t <= this.length - blend) return;
    // Schleife: in den letzten Bruchteilen einer Sekunde weich in das erste Bild überblenden, damit beim Neubeginn
    // nichts springt. Die waagrechte Lage der Hüfte bleibt (sonst liefe die Figur zurück), außer an Ort und Stelle.
    const u = (t - (this.length - blend)) / blend, w = u * u * (3 - 2 * u);
    const end = this.capture();
    this.reset();
    this.pose(0);
    const start = this.capture();
    for (const [name, bone] of Object.entries(this.target)) {
      bone.quaternion.copy(end[name][0]).slerp(start[name][0], w);
      if (name === 'Hips') {
        const p = end[name][1].clone().lerp(start[name][1], w);
        if (!this.options.inPlace && !this.holder) {
          // nur die Höhe überblenden: in den Achsen der Szene, dann zurück ins Elternteil
          const parent = bone.parent!;
          parent.updateWorldMatrix(true, false);
          const worldEnd = parent.localToWorld(end[name][1].clone()), worldMix = parent.localToWorld(p.clone());
          worldEnd.y = worldMix.y;
          p.copy(parent.worldToLocal(worldEnd));
        }
        bone.position.copy(p);
      }
    }
    if (this.holder) this.holder.position.copy(end.holder![1]);
    this.target.Hips.updateMatrixWorld(true);
  }

  private capture() {
    const out: Record<string, [THREE.Quaternion, THREE.Vector3]> = {};
    for (const [name, bone] of Object.entries(this.target)) out[name] = [bone.quaternion.clone(), bone.position.clone()];
    if (this.holder) out.holder = [this.holder.quaternion.clone(), this.holder.position.clone()];
    return out;
  }

  private reset() {
    for (const [name, [q, p]] of Object.entries(this.restLocal)) { this.target[name].quaternion.copy(q); this.target[name].position.copy(p); }
    if (this.holder) this.holder.position.copy(this.holderRest);
    this.target.Hips.updateMatrixWorld(true);
  }

  // Übertragen, dann die Füße auf den Boden und, wo sie Kontakt haben, an ihren Platz.
  private pose(t: number) {
    this.raw(t);
    this.ground();
    if (!this.locks.length) return;
    const frame = this.locks[Math.max(0, Math.min(this.locks.length - 1, Math.round(t * FPS)))];
    LEGS.forEach(([up, leg, foot], side) => {
      const lock = frame[side];
      const [a, b, c] = [this.target[up], this.target[leg], this.target[foot]];
      if (!lock || lock.weight <= 0 || !a || !b || !c) return;
      const target = worldP(c).lerp(lock.at, lock.weight);
      reach(a, b, c, target, this.ahead);
    });
  }

  // Sinkt ein Knöchel unter seine Höhe in der Grundhaltung, hebt die Hüfte die Figur um so viel an.
  private ground() {
    const ankles = LEGS.map(([, , foot]) => this.target[foot]).filter(Boolean).map(b => worldP(b).y);
    if (!ankles.length) return;
    const deficit = this.ankle - Math.min(...ankles);
    if (deficit <= 1e-4) return;
    const hips = this.target.Hips;
    hips.parent!.updateWorldMatrix(true, false);
    hips.position.copy(hips.parent!.worldToLocal(worldP(hips).add(new THREE.Vector3(0, deficit, 0))));
    hips.updateMatrixWorld(true);
  }

  // Einmal durch die Aufnahme: wann steht welcher Fuß (Knöchel tief und fast still)? Dort wird er an der Stelle
  // festgehalten, an der er aufsetzt; drei Bilder davor und danach blendet das weich ein und aus.
  private findContacts() {
    const frames = Math.max(2, Math.round(this.length * FPS) + 1);
    const ankles: THREE.Vector3[][] = [];
    for (let i = 0; i < frames; i++) {
      this.reset();
      this.raw(i / FPS);
      this.ground();
      ankles.push(LEGS.map(([, , foot]) => (this.target[foot] ? worldP(this.target[foot]) : new THREE.Vector3())));
    }
    this.reset();
    const high = 0.07 * this.hipHeight, slow = 0.6 * this.hipHeight / FPS;
    const ramp = 3;
    this.locks = Array.from({ length: frames }, () => [{ weight: 0, at: new THREE.Vector3() }, { weight: 0, at: new THREE.Vector3() }]);
    for (let side = 0; side < 2; side++) {
      const down = ankles.map((frame, i) => {
        const prev = ankles[Math.max(0, i - 1)][side], next = ankles[Math.min(frames - 1, i + 1)][side];
        const speed = Math.hypot(next.x - prev.x, next.z - prev.z) / Math.max(1, Math.min(frames - 1, i + 1) - Math.max(0, i - 1));
        return frame[side].y - this.ankle < high && speed < slow;
      });
      for (let i = 0; i < frames;) {
        if (!down[i]) { i++; continue; }
        let j = i;
        while (j + 1 < frames && down[j + 1]) j++;
        if (j - i >= 2) {
          const at = ankles[i][side].clone();
          at.y = Math.max(this.ankle, Math.min(at.y, ...ankles.slice(i, j + 1).map(f => f[side].y)));
          for (let k = Math.max(0, i - ramp); k <= Math.min(frames - 1, j + ramp); k++) {
            const w = k < i ? 1 - (i - k) / (ramp + 1) : k > j ? 1 - (k - j) / (ramp + 1) : 1;
            if (w > this.locks[k][side].weight) this.locks[k][side] = { weight: w, at };
          }
        }
        i = j + 1;
      }
    }
  }

  // Die reine Übertragung ohne Füße: Drehungen in den Achsen der Szene, Hüfte nach Beinlänge, Arme nach armSpace.
  private raw(t: number) {
    const motion = this.motion;
    motion.at(this.options.start + t * this.options.speed);
    const spread = THREE.MathUtils.degToRad(this.options.armSpace || 0);
    const neck = this.neckSwing();
    for (const name of ORDER) {
      const bone = this.target[name], from = motion.bones[this.source(name)];
      if (!bone || !from || !bone.parent) continue;
      const own = worldQ(from).multiply(motion.rest.q[this.source(name)].clone().invert());
      const delta = this.rotation(neck && NECK.includes(name) ? neck : own);
      let world = delta.multiply(this.align[name]).multiply(this.rest.q[name]);
      if (spread && ARMS.includes(name)) {
        // Der ganze Arm dreht um die Schulter nach außen (alle Knochen des Arms um dieselbe Achse in der Szene).
        const axis = this.outward[name.startsWith('Left') ? 'Left' : 'Right'];
        if (axis) world = new THREE.Quaternion().setFromAxisAngle(axis, spread).multiply(world);
      }
      bone.parent.updateWorldMatrix(true, false);
      bone.quaternion.copy(worldQ(bone.parent).invert().multiply(world));
      if (name === 'Hips') {
        const now = worldP(from);
        const moved = this.direction(now.clone().sub(motion.frame0)).multiplyScalar(this.scale);
        const at = new THREE.Vector3(this.rest.p.Hips.x, this.ankle + (now.y - motion.ankle0) * this.scale, this.rest.p.Hips.z);
        if (!this.options.inPlace) {
          if (this.holder) {
            // Root Motion: das Objekt über der Hüfte wandert waagrecht, die Hüfte darin nur noch auf und ab.
            const parent = this.holder.parent!;
            parent.updateWorldMatrix(true, false);
            const restWorld = parent.localToWorld(this.holderRest.clone());
            this.holder.position.copy(parent.worldToLocal(restWorld.add(new THREE.Vector3(moved.x, 0, moved.z))));
            this.holder.updateMatrixWorld(true);
          }
          at.add(new THREE.Vector3(moved.x, 0, moved.z));
        }
        bone.parent.updateWorldMatrix(true, false);
        bone.position.copy(bone.parent.worldToLocal(at));
      }
      bone.updateMatrixWorld(true);
    }
  }

  // Wie sich die Linie vom Hals- zum Kopfgelenk der Aufnahme gegen ihre Ruhehaltung neigt (in den Achsen der Aufnahme).
  private neckSwing() {
    const motion = this.motion, neck = motion.bones[this.source('Neck')], head = motion.bones[this.source('Head')];
    if (!neck || !head) return null;
    const rest = motion.rest.p[this.source('Head')].clone().sub(motion.rest.p[this.source('Neck')]).normalize();
    return new THREE.Quaternion().setFromUnitVectors(rest, worldP(head).sub(worldP(neck)).normalize());
  }

  // Für Root Motion: das Objekt über der Hüfte, wenn es mitbewegt wird.
  get mover() {
    return this.holder;
  }
}
