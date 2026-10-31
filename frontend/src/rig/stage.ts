// Die Bühne für ein GLB mit Skelett: Szene, Kamera, Proben (Gehen, Schlag, Tritt, Arme heben, Kniebeuge, Umfallen)
// und Knochen von
// Hand. Dieselbe Logik wie der Rig-Prüfstand: die Proben drehen um die Achsen der Szene, nicht der Knochen, und
// gehen von der Grundhaltung der Figur aus (T- oder A-Pose), die beim Laden gemessen wird.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export type Clip = 'walk' | 'punch' | 'kick' | 'arms' | 'squat' | 'fall';
export type Track = { node: number; path: 'rotation' | 'translation'; values: number[] };
export type Baked = { name: string; times: number[]; tracks: Track[] };
// Eine Haltung in Grad je Knochen, [x, y, z] in Worten statt Achsen (wie Gemma sie schreibt, omat3d/assist.py):
// Rumpf, Hals, Kopf: x nach vorn neigen, y nach links drehen, z zur linken Seite neigen. Oberarm: z abspreizen (0 hängt,
// 90 waagrecht), x nach vorn heben. Unterarm: x Ellbogen beugen, z nach außen und oben. Oberschenkel: x nach vorn,
// z nach außen. Knie: x beugen. Fuß: x Zehen heben. Links und rechts aus Sicht der Figur.
export type Pose = Record<string, [number, number, number]>;
export type PoseName = 'down' | 'relaxed' | 'hero' | 'stride' | 'wave';
export const POSES: Record<PoseName, Pose> = {
  down: { LeftArm: [0, 0, 6], RightArm: [0, 0, 6], LeftForeArm: [8, 0, 0], RightForeArm: [8, 0, 0] },
  relaxed: {
    Spine: [0, 5, 3], Neck: [3, -8, 0], LeftArm: [5, 0, 8], RightArm: [-5, 0, 10], LeftForeArm: [15, 0, 0],
    RightForeArm: [10, 0, 0], LeftUpLeg: [8, 0, 2], LeftLeg: [14, 0, 0], RightUpLeg: [0, 0, 3],
  },
  hero: {
    Spine: [-4, 0, 0], Spine2: [-4, 0, 0], Neck: [-4, 15, 0], LeftArm: [0, 0, 22], RightArm: [0, 0, 22],
    LeftForeArm: [25, 0, 0], RightForeArm: [25, 0, 0], LeftUpLeg: [0, 0, 9], RightUpLeg: [0, 0, 9],
  },
  stride: {
    Spine: [3, -6, 0], LeftArm: [-15, 0, 8], RightArm: [18, 0, 8], LeftForeArm: [10, 0, 0], RightForeArm: [20, 0, 0],
    LeftUpLeg: [22, 0, 0], LeftLeg: [12, 0, 0], RightUpLeg: [-14, 0, 0], RightLeg: [6, 0, 0],
  },
  wave: {
    Neck: [0, -10, 0], LeftArm: [0, 0, 8], LeftForeArm: [10, 0, 0], RightArm: [0, 0, 80], RightForeArm: [0, 0, 75],
  },
};
// Eltern vor Kindern, damit jeder Knochen auf der schon gedrehten Lage seines Elternteils aufsetzt.
const POSE_ORDER = ['Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head', 'LeftArm', 'RightArm', 'LeftForeArm', 'RightForeArm',
  'LeftUpLeg', 'RightUpLeg', 'LeftLeg', 'RightLeg', 'LeftFoot', 'RightFoot'];

export type Facts = { bones: number; meshes: number; vertices: number; core: { name: string; found: boolean }[] };

// Mixamo-Namen, wie skintokens sie mit --class humanoid vergibt (mit oder ohne "mixamorig:").
const CORE = ['Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head', 'LeftShoulder', 'LeftArm', 'LeftForeArm', 'LeftHand',
  'RightShoulder', 'RightArm', 'RightForeArm', 'RightHand', 'LeftUpLeg', 'LeftLeg', 'LeftFoot', 'RightUpLeg', 'RightLeg', 'RightFoot'];

// 0 bis 1 weich, außerhalb festgehalten.
const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

// Eine Runde jeder Probe in Sekunden, für den Export als Schleife.
export const PERIODS: Record<Clip, number> = {
  walk: (2 * Math.PI) / 5, punch: 1.4, kick: 1.8, arms: Math.PI, squat: (2 * Math.PI) / 2.4, fall: 4.5,
};

export class RigStage {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
  private controls: OrbitControls;
  private root: THREE.Object3D | null = null;
  private helper: THREE.SkeletonHelper | null = null;
  private meshes: THREE.SkinnedMesh[] = [];
  private rest = new Map<THREE.Bone, THREE.Quaternion>();
  private restPosition = new Map<THREE.Bone, THREE.Vector3>();
  // Die zuletzt gewählte Haltung; die Schieber drehen von ihr aus, nicht von der Grundhaltung.
  private base = new Map<THREE.Bone, THREE.Quaternion>();
  // Ober- und Unterschenkel in Bühnenmaßen, damit die Hüfte so weit sinkt, dass das Standbein am Boden bleibt.
  private thigh = 0.4;
  private shin = 0.4;
  // Die Lage der ganzen Figur nach dem Einpassen; Umfallen kippt sie um die Fersen und stellt sie danach zurück.
  private rootPosition = new THREE.Vector3();
  private rootQuaternion = new THREE.Quaternion();
  private heel = 0;
  // Welcher Knoten des GLB zu welchem Knochen gehört, für den Export der Proben.
  private nodes = new Map<THREE.Bone, number>();
  private armAngle: Record<string, number> = { LeftArm: Math.PI / 2, RightArm: -Math.PI / 2 };
  private clip: Clip | null = null;
  private started = 0;
  private frame = 0;
  private resizer: ResizeObserver;
  bones: THREE.Bone[] = [];
  amplitude = 1;

  constructor(private canvas: HTMLCanvasElement, boneColor: string) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x445044, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(2, 4, 3);
    this.scene.add(sun);
    const grid = new THREE.GridHelper(4, 16, 0x888888, 0x888888);
    (grid.material as THREE.Material).opacity = 0.25;
    (grid.material as THREE.Material).transparent = true;
    this.scene.add(grid);
    this.boneColor = boneColor;
    this.resizer = new ResizeObserver(() => this.resize());
    this.resizer.observe(canvas);
    const loop = (now: number) => {
      if (this.clip) { this.resetPose(); this.play(this.clip, (now - this.started) / 1000); }
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      this.frame = requestAnimationFrame(loop);
    };
    this.frame = requestAnimationFrame(loop);
  }

  private boneColor: string;

  setBoneColor(color: string) {
    this.boneColor = color;
    if (this.helper) (this.helper.material as THREE.LineBasicMaterial).color = new THREE.Color(color);
  }

  dispose() {
    cancelAnimationFrame(this.frame);
    this.resizer.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
  }

  private resize() {
    const { clientWidth: w, clientHeight: h } = this.canvas;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  async load(url: string): Promise<Facts> {
    const gltf = await new GLTFLoader().loadAsync(url);
    if (this.root) this.scene.remove(this.root);
    if (this.helper) this.scene.remove(this.helper);
    this.root = gltf.scene;
    this.scene.add(this.root);
    this.fit(this.root);
    this.bones = []; this.meshes = []; this.rest.clear(); this.base.clear();
    this.root.traverse(object => {
      if ((object as THREE.Bone).isBone) this.bones.push(object as THREE.Bone);
      if ((object as THREE.SkinnedMesh).isSkinnedMesh) this.meshes.push(object as THREE.SkinnedMesh);
    });
    this.bones.forEach(bone => this.rest.set(bone, bone.quaternion.clone()));
    this.restPosition.clear();
    this.bones.forEach(bone => this.restPosition.set(bone, bone.position.clone()));
    this.measureLegs();
    this.nodes.clear();
    this.bones.forEach(bone => {
      const node = gltf.parser.associations.get(bone)?.nodes;
      if (node !== undefined) this.nodes.set(bone, node);
    });
    this.measureArms();
    this.helper = new THREE.SkeletonHelper(this.root);
    (this.helper.material as THREE.LineBasicMaterial).depthTest = false;
    (this.helper.material as THREE.LineBasicMaterial).color = new THREE.Color(this.boneColor);
    this.scene.add(this.helper);
    return {
      bones: this.bones.length,
      meshes: this.meshes.length,
      vertices: this.meshes.reduce((sum, mesh) => sum + mesh.geometry.attributes.position.count, 0),
      core: CORE.map(name => ({ name, found: !!this.find(name) })),
    };
  }

  private fit(object: THREE.Object3D) {
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    // Erst skalieren, dann verschieben: so steht die Figur mit den Füßen auf dem Gitter, mittig, 1,8 hoch.
    const scale = 1.8 / (Math.max(size.x, size.y, size.z) || 1);
    object.scale.multiplyScalar(scale);
    object.position.sub(new THREE.Vector3(center.x, box.min.y, center.z)).multiplyScalar(scale);
    this.rootPosition.copy(object.position);
    this.rootQuaternion.copy(object.quaternion);
    this.heel = (box.min.z - center.z) * scale;
    this.controls.target.set(0, 0.9, 0);
    this.camera.position.set(1.6, 1.3, 3.2);
    this.controls.update();
  }

  find(name: string) {
    return this.bones.find(bone => bone.name.replace(/^mixamorig[:_]?/i, '') === name);
  }

  // Wie weit die Oberarme in der Grundhaltung von der Senkrechten abstehen, aus der Lage von Ober- zu Unterarm.
  private measureArms() {
    this.root?.updateMatrixWorld(true);
    for (const [arm, fore] of [['LeftArm', 'LeftForeArm'], ['RightArm', 'RightForeArm']]) {
      const a = this.find(arm), f = this.find(fore);
      if (!a || !f) continue;
      const d = f.getWorldPosition(new THREE.Vector3()).sub(a.getWorldPosition(new THREE.Vector3()));
      this.armAngle[arm] = Math.atan2(d.x, -d.y);
    }
  }

  private measureLegs() {
    const at = (name: string) => this.find(name)?.getWorldPosition(new THREE.Vector3());
    const [hip, knee, ankle] = [at('LeftUpLeg'), at('LeftLeg'), at('LeftFoot')];
    if (hip && knee && ankle) {
      this.thigh = hip.distanceTo(knee);
      this.shin = knee.distanceTo(ankle);
    }
  }

  // Wie weit die Hüfte sinken muss, damit ein Bein noch bis zum Boden reicht: thigh und knee wie in turn() um X
  // (Oberschenkel negativ nach vorn, Knie positiv gebeugt), der Unterschenkel steht dann um thigh + knee schräg.
  private legDrop(thigh: number, knee: number) {
    const a = this.amplitude;
    return this.thigh + this.shin - this.thigh * Math.cos(thigh * a) - this.shin * Math.cos((thigh + knee) * a);
  }

  // Die Hüfte um dy (Bühnenmaß) senken, entlang der Senkrechten der Szene.
  private sink(dy: number) {
    const hips = this.find('Hips');
    if (!hips?.parent || dy <= 0) return;
    hips.parent.updateWorldMatrix(true, false);
    const target = hips.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, -dy, 0));
    hips.position.copy(hips.parent.worldToLocal(target));
  }

  private toArm(arm: string, angle: number) {
    return (Math.sign(this.armAngle[arm]) || (arm === 'LeftArm' ? 1 : -1)) * angle - this.armAngle[arm];
  }

  // Die Figur schaut nach +Z (so liefert TRELLIS.2 sie). X: vor/zurück (positiv kippt nach vorn, was nach oben zeigt,
  // und nach hinten, was nach unten hängt), Y: um die Hochachse, Z: seitwärts.
  // Eine Haltung (siehe Pose) aus der Grundhaltung heraus; danach stehen die Füße wieder auf dem Gitter.
  applyPose(pose: Pose) {
    this.clip = null;
    this.resetPose();
    const amplitude = this.amplitude;
    this.amplitude = 1;
    const rad = THREE.MathUtils.degToRad;
    for (const name of POSE_ORDER) {
      if (!pose[name]) continue;
      const [x, y, z] = pose[name].map(rad);
      const side = name.startsWith('Left') ? 1 : -1;
      if (/^(Hips|Spine\d?|Neck|Head)$/.test(name)) this.turn(name, x, y, -z);
      else if (/^(Left|Right)Arm$/.test(name)) this.turn(name, -x, y, this.toArm(name, z));
      else if (/^(Left|Right)Leg$/.test(name)) this.turn(name, x, y, side * z);
      else this.turn(name, -x, y, side * z);
    }
    this.amplitude = amplitude;
    this.base = new Map(this.bones.map(bone => [bone, bone.quaternion.clone()]));
    this.ground();
  }

  // Die Figur so heben oder senken, dass ihr tiefster Punkt (mit Skelett gerechnet) auf dem Gitter liegt.
  ground() {
    if (!this.root) return;
    this.root.position.copy(this.rootPosition);
    this.root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(this.root, true);
    if (Number.isFinite(box.min.y)) this.root.position.y -= box.min.y;
  }

  // Die Drehung jedes Knochens, wie er gerade steht, für die Druckdatei: Index des Knotens im GLB -> x, y, z, w.
  printPose(): Record<string, [number, number, number, number]> {
    const pose: Record<string, [number, number, number, number]> = {};
    for (const [bone, node] of this.nodes) pose[node] = [bone.quaternion.x, bone.quaternion.y, bone.quaternion.z, bone.quaternion.w];
    return pose;
  }

  private turn(name: string, x = 0, y = 0, z = 0) {
    const bone = this.find(name);
    if (!bone || !bone.parent) return;
    bone.parent.updateWorldMatrix(true, false);
    const parent = bone.parent.getWorldQuaternion(new THREE.Quaternion());
    const restWorld = parent.clone().multiply(this.rest.get(bone)!);
    const a = this.amplitude;
    const world = new THREE.Quaternion().setFromEuler(new THREE.Euler(x * a, y * a, z * a));
    bone.quaternion.copy(parent.invert().multiply(world.multiply(restWorld)));
  }

  private play(clip: Clip, t: number) {
    const turn = this.turn.bind(this), toArm = this.toArm.bind(this);
    if (clip === 'walk') {
      // Arme und Beine gegengleich; das Bein, das nach vorn schwingt, beugt das Knie, das Standbein bleibt gestreckt
      // und die Hüfte sinkt so weit, dass sein Fuß am Boden bleibt (sonst schweben beide Füße abwechselnd).
      const s = Math.sin(t * 5), stride = 0.42;
      const knee = (phase: number) => 0.95 * Math.max(0, Math.sin(t * 5 + phase)) ** 1.5;
      const [leftKnee, rightKnee] = [knee(1.6), knee(1.6 + Math.PI)];
      turn('Spine', 0.05, 0.1 * s); turn('Neck', 0, -0.08 * s);
      turn('LeftArm', 0.4 * s, 0, toArm('LeftArm', 0.12)); turn('RightArm', -0.4 * s, 0, toArm('RightArm', 0.12));
      turn('LeftForeArm', -0.25 - 0.2 * Math.max(0, -s)); turn('RightForeArm', -0.25 - 0.2 * Math.max(0, s));
      turn('LeftUpLeg', -stride * s); turn('RightUpLeg', stride * s);
      turn('LeftLeg', leftKnee); turn('RightLeg', rightKnee);
      // Füße fast flach halten, sonst stechen die Zehen des Standbeins in den Boden.
      turn('LeftFoot', -0.85 * (-stride * s + leftKnee)); turn('RightFoot', -0.85 * (stride * s + rightKnee));
      // Steht das Bein mit dem gestreckteren Knie am Boden: daraus die Hüfthöhe.
      this.sink(Math.min(this.legDrop(-stride * s, leftKnee), this.legDrop(stride * s, rightKnee)));
    } else if (clip === 'punch') {
      // Deckung wie beim Boxen: Ellbogen unten, Unterarme hoch, Fäuste vor dem Kinn. Der rechte Arm stößt gerade
      // nach vorn zur Mitte, die Schultern drehen mit, das Gewicht geht leicht nach vorn; der linke bleibt oben.
      const c = (t % 1.4) / 1.4;
      const p = ease(c / 0.18) * (1 - ease((c - 0.32) / 0.3));
      turn('Spine', 0.08 + 0.08 * p, 0.3 * p); turn('Spine1', 0, 0.15 * p); turn('Neck', 0, -0.3 * p);
      // Y dreht einen nach vorn zeigenden Arm: positiv nach links, also beim rechten Arm zur Mitte.
      turn('LeftArm', -0.55, -0.25, toArm('LeftArm', 0.25)); turn('LeftForeArm', -2.2);
      turn('RightArm', -0.55 - 1.0 * p, 0.25 + 0.25 * p, toArm('RightArm', 0.25 - 0.4 * p));
      turn('RightForeArm', -2.2 * (1 - p));
      turn('LeftUpLeg', -0.15); turn('LeftLeg', 0.2); turn('RightUpLeg', 0.1); turn('RightLeg', 0.15);
      turn('LeftFoot', -0.05); turn('RightFoot', -0.25);
      this.sink(Math.min(this.legDrop(-0.15, 0.2), this.legDrop(0.1, 0.15)));
    } else if (clip === 'kick') {
      // Knie anziehen, Unterschenkel vorschnellen, zurück; das Standbein gibt etwas nach, die Arme halten Balance.
      const c = (t % 1.8) / 1.8;
      const up = ease(c / 0.3) * (1 - ease((c - 0.62) / 0.3));
      const snap = ease((c - 0.3) / 0.1) * (1 - ease((c - 0.48) / 0.14));
      turn('Spine', -0.25 * up);
      turn('RightUpLeg', -1.45 * up); turn('RightLeg', 1.9 * up * (1 - snap)); turn('RightFoot', 0.5 * snap);
      turn('LeftLeg', 0.2 * up); turn('LeftUpLeg', -0.1 * up);
      this.sink(this.legDrop(-0.1 * up, 0.2 * up));
      turn('LeftArm', -0.5 * up, 0, toArm('LeftArm', 0.5 * up + 0.2)); turn('RightArm', 0.4 * up, 0, toArm('RightArm', 0.6 * up + 0.2));
      turn('LeftForeArm', -0.9 * up); turn('RightForeArm', -0.5 * up);
    } else if (clip === 'fall') {
      // Nach hinten umfallen wie ein Brett: mit der Schwerkraft schneller werdend, liegen bleiben, wieder aufstehen.
      const c = t % 4.5;
      const f = c < 1 ? c * c : c < 3.2 ? 1 : 1 - ease((c - 3.2) / 1.1);
      turn('Neck', 0.35 * f); turn('Spine', 0.15 * f);
      turn('LeftArm', 0, 0, toArm('LeftArm', 0.2 + 1.1 * f)); turn('RightArm', 0, 0, toArm('RightArm', 0.2 + 1.1 * f));
      turn('LeftUpLeg', -0.25 * f); turn('RightUpLeg', -0.1 * f); turn('LeftLeg', 0.35 * f); turn('RightLeg', 0.15 * f);
      this.tilt(-(Math.PI / 2) * 0.93 * f);
    } else if (clip === 'arms') {
      const lift = 0.2 + ((1 - Math.cos(t * 2)) / 2) * 1.7;
      turn('LeftArm', 0, 0, toArm('LeftArm', lift)); turn('RightArm', 0, 0, toArm('RightArm', lift));
    } else {
      const p = (1 - Math.cos(t * 2.4)) / 2;
      turn('Spine', -0.35 * p);
      turn('LeftArm', -1.4 * p, 0, toArm('LeftArm', 0.2)); turn('RightArm', -1.4 * p, 0, toArm('RightArm', 0.2));
      turn('LeftUpLeg', -1.3 * p); turn('RightUpLeg', -1.3 * p);
      turn('LeftLeg', 2.1 * p); turn('RightLeg', 2.1 * p);
      turn('LeftFoot', -0.8 * p); turn('RightFoot', -0.8 * p);
      this.sink(this.legDrop(-1.3 * p, 2.1 * p));
    }
  }

  // Kippt die ganze Figur um die X-Achse durch die Fersen. Nach den Knochen, damit turn() noch die stehende Figur sah.
  private tilt(angle: number) {
    if (!this.root) return;
    const turn = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), angle * this.amplitude);
    const pivot = new THREE.Vector3(0, 0, this.heel);
    this.root.quaternion.copy(turn).multiply(this.rootQuaternion);
    this.root.position.copy(this.rootPosition).sub(pivot).applyQuaternion(turn).add(pivot);
  }

  // Die Proben als Keyframes (30 je Sekunde, eine Runde) in den Koordinaten des GLB, ohne das Einpassen auf der
  // Bühne: Drehungen der Knochen, dazu für die obersten Knochen die Lage, weil Umfallen die ganze Figur bewegt.
  bake(clips: [Clip, string][], fps = 30): Baked[] {
    const playing = this.clip;
    this.clip = null;
    const current = this.bones.map(bone => [bone.quaternion.clone(), bone.position.clone()] as const);
    const placed = this.root?.position.clone();
    const top = this.bones.filter(bone => !(bone.parent as THREE.Bone | null)?.isBone && this.nodes.has(bone));
    const baked = clips.map(([clip, name]) => {
      const frames = Math.max(2, Math.round(PERIODS[clip] * fps));
      const times = Array.from({ length: frames + 1 }, (_, i) => (i * PERIODS[clip]) / frames);
      const rotations = new Map<THREE.Bone, number[]>();
      const positions = new Map<THREE.Bone, number[]>();
      for (const t of times) {
        this.resetPose();
        this.play(clip, t);
        this.root!.updateMatrixWorld(true);
        const world = new Map(top.map(bone => [bone, bone.matrixWorld.clone()]));
        // Die Bühne zurück in ihre Lage, damit die Kippung beim Umfallen in den obersten Knochen landet.
        this.root!.position.copy(this.rootPosition);
        this.root!.quaternion.copy(this.rootQuaternion);
        this.root!.updateMatrixWorld(true);
        for (const bone of this.nodes.keys()) {
          let q = bone.quaternion;
          if (world.has(bone)) {
            const local = bone.parent!.matrixWorld.clone().invert().multiply(world.get(bone)!);
            const p = new THREE.Vector3(); q = new THREE.Quaternion();
            local.decompose(p, q, new THREE.Vector3());
            positions.set(bone, [...(positions.get(bone) ?? []), p.x, p.y, p.z]);
          }
          rotations.set(bone, [...(rotations.get(bone) ?? []), q.x, q.y, q.z, q.w]);
        }
      }
      const tracks: Track[] = [];
      const moves = (values: number[], size: number) => values.some((v, i) => Math.abs(v - values[i % size]) > 1e-5);
      for (const [bone, values] of rotations) if (moves(values, 4)) tracks.push({ node: this.nodes.get(bone)!, path: 'rotation', values });
      for (const [bone, values] of positions) if (moves(values, 3)) tracks.push({ node: this.nodes.get(bone)!, path: 'translation', values });
      return { name, times, tracks };
    });
    this.resetPose();
    this.bones.forEach((bone, i) => { bone.quaternion.copy(current[i][0]); bone.position.copy(current[i][1]); });
    if (placed) this.root?.position.copy(placed);
    this.clip = playing;
    return baked;
  }

  setClip(clip: Clip | null) {
    this.base.clear();
    this.resetPose();
    this.clip = clip;
    this.started = performance.now();
  }

  resetPose() {
    this.bones.forEach(bone => {
      bone.quaternion.copy(this.rest.get(bone)!);
      bone.position.copy(this.restPosition.get(bone)!);
    });
    this.root?.position.copy(this.rootPosition);
    this.root?.quaternion.copy(this.rootQuaternion);
  }

  // Drehung eines Knochens um seine eigenen Achsen, in Grad, gegenüber der Grundhaltung.
  rotate(index: number, degrees: [number, number, number]) {
    const bone = this.bones[index];
    if (!bone) return;
    this.clip = null;
    const [x, y, z] = degrees.map(THREE.MathUtils.degToRad);
    const from = this.base.get(bone) ?? this.rest.get(bone)!;
    bone.quaternion.copy(from).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z)));
  }

  showSkeleton(on: boolean) {
    if (this.helper) this.helper.visible = on;
  }

  wireframe(on: boolean) {
    this.meshes.forEach(mesh => ([] as THREE.Material[]).concat(mesh.material).forEach(material => {
      (material as THREE.MeshStandardMaterial).wireframe = on;
    }));
  }
}
