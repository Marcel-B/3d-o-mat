// Eine Aufnahme (BVH aus der Bewegungsbibliothek, omat3d/motions.py) auf das Skelett der Figur übertragen.
// Die Knochen der Aufnahme heißen je nach Sammlung anders; sie werden auf Mixamo-Namen abgebildet. Dann wird für jeden
// Knochen die Ruhehaltung angeglichen (die Richtung zum Kind in beiden Skeletten, so passt eine T-Pose-Aufnahme auch auf
// eine Figur in A-Pose), jede Drehung relativ zur Ruhehaltung in den Achsen der Szene übertragen und die Hüftbahn mit
// dem Verhältnis der Beinlängen skaliert. Schaut die Figur anders als die Aufnahme, dreht G die Aufnahme um die
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
// Wohin ein Knochen zeigt: zum ersten dieser Kinder, das beide Skelette haben (Bandai hat nur zwei Rückenknochen).
const CHILD: Record<string, string[]> = {
  Spine: ['Spine1', 'Spine2', 'Neck'], Spine1: ['Spine2', 'Neck'], Spine2: ['Neck'], Neck: ['Head'],
  LeftShoulder: ['LeftArm'], LeftArm: ['LeftForeArm'], LeftForeArm: ['LeftHand'],
  RightShoulder: ['RightArm'], RightArm: ['RightForeArm'], RightForeArm: ['RightHand'],
  LeftUpLeg: ['LeftLeg'], LeftLeg: ['LeftFoot'], LeftFoot: ['LeftToeBase'],
  RightUpLeg: ['RightLeg'], RightLeg: ['RightFoot'], RightFoot: ['RightToeBase'],
};
// Knochen ohne Kind übernehmen den Ausgleich ihres Elternteils, sonst knickt etwa die Hand gegen den Unterarm ab.
const PARENT: Record<string, string> = {
  Head: 'Neck', LeftHand: 'LeftForeArm', RightHand: 'RightForeArm', LeftToeBase: 'LeftFoot', RightToeBase: 'RightFoot',
};
const mirrored = (name: string) => name.replace(/^Left/, '\0').replace(/^Right/, 'Left').replace('\0', 'Right');

export type MotionOptions = { speed: number; inPlace: boolean; mirror: boolean; start: number; end: number };

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

  constructor(find: (name: string) => THREE.Bone | undefined, private motion: Motion, private options: MotionOptions) {
    for (const name of ORDER) {
      const bone = find(name);
      if (bone) this.target[name] = bone;
    }
    if (!this.target.Hips) throw new Error('Figur ohne Hips');
    this.target.Hips.parent?.updateMatrixWorld(true);
    this.target.Hips.updateMatrixWorld(true);
    this.rest = measure(this.target);
    const feet = ['LeftFoot', 'RightFoot'].filter(n => this.rest.p[n]).map(n => this.rest.p[n].y);
    this.ankle = feet.length ? Math.min(...feet) : 0;
    this.hipHeight = this.rest.p.Hips.y - this.ankle || 1;
    this.scale = this.hipHeight / (motion.hipHeight || 1);
    const ahead = this.rest.p.LeftUpLeg && this.rest.p.RightUpLeg ? facing(this.rest.p) : new THREE.Vector3(0, 0, 1);
    this.G.setFromUnitVectors(facing(motion.rest.p), ahead);
    this.Gi.copy(this.G).invert();
    this.normal.crossVectors(new THREE.Vector3(0, 1, 0), ahead).normalize();
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
    const motion = this.motion;
    motion.at(this.options.start + t * this.options.speed);
    for (const name of ORDER) {
      const bone = this.target[name], from = motion.bones[this.source(name)];
      if (!bone || !from || !bone.parent) continue;
      const delta = this.rotation(worldQ(from).multiply(motion.rest.q[this.source(name)].clone().invert()));
      const world = delta.multiply(this.align[name]).multiply(this.rest.q[name]);
      bone.parent.updateWorldMatrix(true, false);
      bone.quaternion.copy(worldQ(bone.parent).invert().multiply(world));
      if (name === 'Hips') {
        const now = worldP(from);
        const moved = this.direction(now.clone().sub(motion.frame0)).multiplyScalar(this.scale);
        const at = new THREE.Vector3(this.rest.p.Hips.x, this.ankle + (now.y - motion.ankle0) * this.scale, this.rest.p.Hips.z);
        if (!this.options.inPlace) at.add(new THREE.Vector3(moved.x, 0, moved.z));
        bone.position.copy(bone.parent.worldToLocal(at));
      }
      bone.updateMatrixWorld(true);
    }
  }
}
