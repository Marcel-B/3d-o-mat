// Die Bühne für ein GLB mit Skelett: Szene, Kamera, Proben (Gehen, Schlag, Tritt, Arme heben, Kniebeuge, Umfallen)
// und Knochen von
// Hand. Dieselbe Logik wie der Rig-Prüfstand: die Proben drehen um die Achsen der Szene, nicht der Knochen, und
// gehen von der Grundhaltung der Figur aus (T- oder A-Pose), die beim Laden gemessen wird.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export type Clip = 'walk' | 'punch' | 'kick' | 'roundhouse' | 'arms' | 'squat' | 'fall';
export type Track = { node: number; path: 'rotation' | 'translation'; values: number[] };
export type Baked = { name: string; times: number[]; tracks: Track[] };
// Eine Haltung in Grad je Knochen, [x, y, z] in Worten statt Achsen (wie Gemma sie schreibt, omat3d/assist.py):
// Rumpf, Hals, Kopf: x nach vorn neigen, y nach links drehen, z zur linken Seite neigen. Oberarm: x heben (0 hängt,
// 90 waagrecht, 180 senkrecht nach oben), z wohin (0 nach vorn, 90 zur Seite, 180 nach hinten), y um sich selbst drehen.
// Unterarm: x Ellbogen beugen, z nach außen und oben. Oberschenkel: x nach vorn, z nach außen. Knie: x beugen.
// Fuß: x Zehen heben. Links und rechts aus Sicht der Figur.
export type Pose = Record<string, [number, number, number]>;
export type PoseName = 'down' | 'relaxed' | 'hero' | 'stride' | 'wave' | 'cheer';
export const POSES: Record<PoseName, Pose> = {
  down: { LeftArm: [6, 0, 90], RightArm: [6, 0, 90], LeftForeArm: [8, 0, 0], RightForeArm: [8, 0, 0] },
  relaxed: {
    Spine: [0, 5, 3], Neck: [3, -8, 0], LeftArm: [9, 0, 60], RightArm: [11, 0, 115], LeftForeArm: [15, 0, 0],
    RightForeArm: [10, 0, 0], LeftUpLeg: [8, 0, 2], LeftLeg: [14, 0, 0], RightUpLeg: [0, 0, 3],
  },
  hero: {
    Spine: [-4, 0, 0], Spine2: [-4, 0, 0], Neck: [-4, 15, 0], LeftArm: [22, 0, 90], RightArm: [22, 0, 90],
    LeftForeArm: [25, 0, 0], RightForeArm: [25, 0, 0], LeftUpLeg: [0, 0, 9], RightUpLeg: [0, 0, 9],
  },
  stride: {
    Spine: [3, -6, 0], LeftArm: [17, 0, 150], RightArm: [20, 0, 25], LeftForeArm: [10, 0, 0], RightForeArm: [20, 0, 0],
    LeftUpLeg: [22, 0, 0], LeftLeg: [12, 0, 0], RightUpLeg: [-14, 0, 0], RightLeg: [6, 0, 0],
  },
  wave: {
    Neck: [0, -10, 0], LeftArm: [8, 0, 90], LeftForeArm: [10, 0, 0], RightArm: [80, 0, 90], RightForeArm: [0, 0, 75],
  },
  cheer: { Neck: [-15, 0, 0], Head: [-10, 0, 0], LeftArm: [8, 0, 90], LeftForeArm: [10, 0, 0], RightArm: [175, 0, 80] },
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
// 0 bis 1, schnell los und weich am Ziel: für Stöße, die Wucht haben sollen.
const snapOut = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - (1 - x) ** 3);
// Ein kurzer Ausschlag über das Ziel hinaus, um at herum, width breit (0 bis 1 bis 0).
const bump = (x: number, at: number, width: number) => ease((x - at + width) / width) * (1 - ease((x - at) / width));

// Umfallen wie ein Körper, der um die Fersen kippt: Winkel über der Zeit aus θ'' = k·sin θ (k = 3g / 2L bei 1,8 m),
// ab einem kleinen Anstoß, bis er bei FALL_ANGLE am Boden ist. Einmal vorausgerechnet, 600 Schritte je Sekunde.
const FALL_ANGLE = (Math.PI / 2) * 0.93;
const FALL: number[] = (() => {
  const k = (3 * 9.81) / (2 * 1.8), dt = 1 / 600;
  const out = [0.04];
  let angle = 0.04, speed = 0.5;
  while (angle < FALL_ANGLE) {
    speed += k * Math.sin(angle) * dt;
    angle += speed * dt;
    out.push(Math.min(angle, FALL_ANGLE));
  }
  return out;
})();
const FALL_SECONDS = FALL.length / 600;

// Eine Runde jeder Probe in Sekunden, für den Export als Schleife.
export const PERIODS: Record<Clip, number> = {
  walk: (2 * Math.PI) / 5, punch: 1.2, kick: 1.6, roundhouse: 2, arms: Math.PI, squat: (2 * Math.PI) / 2.4, fall: 5,
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
  // Wie hoch der tiefste Knochen in der Grundhaltung über dem Boden liegt (Knöchel, Zehen); beim Liegen ist es der
  // Rücken, der dann ebenso hoch über dem Boden bleibt.
  private floor = 0;
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
    this.floor = this.lowestBone();
    this.measureFingers();
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
      else if (/^(Left|Right)Arm$/.test(name)) this.aim(name, x, y, z, side);
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
    const a = this.amplitude;
    this.turnBy(name, new THREE.Quaternion().setFromEuler(new THREE.Euler(x * a, y * a, z * a)));
  }

  // Den Knochen in der Szene um world drehen, ausgehend von seiner Grundhaltung auf dem (schon gedrehten) Elternteil.
  private turnBy(name: string, world: THREE.Quaternion) {
    const bone = this.find(name);
    if (!bone || !bone.parent) return;
    bone.parent.updateWorldMatrix(true, false);
    const parent = bone.parent.getWorldQuaternion(new THREE.Quaternion());
    const restWorld = parent.clone().multiply(this.rest.get(bone)!);
    bone.quaternion.copy(parent.invert().multiply(world.clone().multiply(restWorld)));
  }

  // Einen Oberarm in eine Richtung zeigen lassen: erst hängt er (aus T- oder A-Pose heruntergeklappt), dann geht er um
  // lift nach oben, zur Seite toward (0 vorn, 90 außen, 180 hinten) und dreht sich um sich selbst um twist (Bogenmaß).
  // Eine Richtung statt zweier Drehungen hintereinander: so kommt „Arm senkrecht hoch“ auch dort an.
  private aim(name: string, lift: number, twist: number, toward: number, side: number) {
    const hang = new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, this.toArm(name, 0)));
    const down = new THREE.Vector3(0, -1, 0);
    const to = new THREE.Vector3(side * Math.sin(lift) * Math.sin(toward), -Math.cos(lift), Math.sin(lift) * Math.cos(toward));
    const raise = new THREE.Quaternion().setFromUnitVectors(down, to.normalize());
    const spin = new THREE.Quaternion().setFromAxisAngle(to, twist);
    this.turnBy(name, spin.multiply(raise).multiply(hang));
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
      // Deckung wie beim Boxen, dann ein gerader Rechter: kurz ausholen (Schultern und Hüfte drehen weg, Knie
      // federn ein), dann schnellt der Arm aus der Hüfte heraus nach vorn, schießt ein wenig über, bleibt einen
      // Moment stehen und kommt zurück in die Deckung. Das Gewicht geht dabei auf das vordere (linke) Bein.
      const c = (t % 1.2) / 1.2;
      const wind = ease(c / 0.2) * (1 - ease((c - 0.2) / 0.07));
      const p = snapOut((c - 0.22) / 0.08) * (1 - ease((c - 0.42) / 0.3)) + 0.12 * bump(c, 0.31, 0.04);
      turn('Hips', 0, -0.12 * wind + 0.22 * p);
      turn('Spine', 0.08 + 0.14 * p, -0.25 * wind + 0.35 * p); turn('Spine1', 0, 0.2 * p);
      turn('Neck', 0.04 * p, 0.3 * wind - 0.55 * p);
      // Y dreht einen nach vorn zeigenden Arm: positiv nach links, also beim rechten Arm zur Mitte.
      turn('LeftArm', -0.55 - 0.1 * p, -0.25, toArm('LeftArm', 0.25)); turn('LeftForeArm', -2.3);
      turn('RightArm', -0.55 + 0.2 * wind - 0.85 * p, 0.25 + 0.25 * p, toArm('RightArm', 0.25 + 0.1 * wind - 0.4 * p));
      turn('RightForeArm', -2.2 * (1 - p) - 0.25 * wind);
      const [leftHip, leftKnee] = [-0.15 - 0.12 * p, 0.2 + 0.2 * wind + 0.15 * p];
      const [rightHip, rightKnee] = [0.1 + 0.12 * p, 0.15 + 0.2 * wind];
      turn('LeftUpLeg', leftHip); turn('LeftLeg', leftKnee); turn('RightUpLeg', rightHip); turn('RightLeg', rightKnee);
      turn('LeftFoot', -0.85 * (leftHip + leftKnee)); turn('RightFoot', -0.25 - 0.25 * p);
      this.fist('Left', 1); this.fist('Right', 1);
      this.sink(Math.min(this.legDrop(leftHip, leftKnee), this.legDrop(rightHip, rightKnee)));
    } else if (clip === 'kick') {
      // Vorwärtstritt: Knie hoch an die Brust ziehen, dann schnellt der Unterschenkel vor und die Hüfte schiebt nach,
      // der Oberkörper lehnt sich als Gegengewicht zurück; kurz stehen, Knie zurück, Fuß absetzen. Die Arme bleiben
      // in der Deckung, der rechte schwingt beim Tritt nach hinten.
      const c = (t % 1.6) / 1.6;
      const up = ease(c / 0.22) * (1 - ease((c - 0.6) / 0.25));
      const snap = snapOut((c - 0.26) / 0.06) * (1 - ease((c - 0.42) / 0.12)) + 0.1 * bump(c, 0.33, 0.04);
      turn('Hips', -0.12 * snap);
      turn('Spine', -0.3 * up - 0.2 * snap); turn('Neck', 0.2 * up + 0.15 * snap);
      turn('RightUpLeg', -1.65 * up - 0.25 * snap); turn('RightLeg', 2.2 * up * (1 - snap)); turn('RightFoot', 0.5 * snap);
      const [hip, knee] = [-0.12 * up, 0.3 * up];
      turn('LeftUpLeg', hip); turn('LeftLeg', knee); turn('LeftFoot', -0.85 * (hip + knee));
      this.sink(this.legDrop(hip, knee));
      turn('LeftArm', -0.6 * up, 0, toArm('LeftArm', 0.35 * up + 0.2)); turn('LeftForeArm', -1.8 * up);
      turn('RightArm', 0.3 * up + 0.45 * snap, 0, toArm('RightArm', 0.45 * up + 0.2)); turn('RightForeArm', -1.2 * up + 0.6 * snap);
      this.fist('Left', up); this.fist('Right', up);
    } else if (clip === 'roundhouse') {
      // Drehtritt mit rechts: das Knie kommt vorn hoch, dann dreht die Figur auf dem linken Fuß gut eine Vierteldrehung
      // ein, der Oberschenkel klappt dabei zur Seite bis waagrecht (die Hüfte dreht über), und der Unterschenkel peitscht
      // im Bogen nach vorn durch. Oberkörper kippt als Gegengewicht weg, Blick und Fäuste bleiben beim Ziel. Zurück:
      // Knie wieder anziehen, zurückdrehen, absetzen.
      const c = (t % 2) / 2;
      const back = ease((c - 0.56) / 0.3);
      const up = ease(c / 0.16) * (1 - back);
      const over = ease((c - 0.1) / 0.18) * (1 - ease((c - 0.6) / 0.22));
      const snap = snapOut((c - 0.3) / 0.07) * (1 - ease((c - 0.46) / 0.12)) + 0.1 * bump(c, 0.37, 0.04);
      const a = this.amplitude;
      const q = (x: number, y: number, z: number) => new THREE.Quaternion().setFromEuler(new THREE.Euler(x * a, y * a, z * a));
      // Drehungen im Raum eines schon gedrehten Körperteils statt im Raum der Szene, damit Bein und Arme mitdrehen.
      const within = (frame: THREE.Quaternion, name: string, rot: THREE.Quaternion) =>
        this.turnBy(name, frame.clone().multiply(rot).multiply(frame.clone().invert()));
      const spin = 1.6 * over + 0.3 * snap;
      const hips = q(0, spin, 0);
      turn('Hips', 0, spin);
      const [hip, knee] = [-0.12 * up, 0.35 * up];
      within(hips, 'LeftUpLeg', q(hip, 0, 0)); within(hips, 'LeftLeg', q(knee, 0, 0));
      within(hips, 'LeftFoot', q(-0.85 * (hip + knee), 0, 0));
      this.sink(this.legDrop(hip, knee));
      const thighRot = q(-1.5 * up * (1 - over) - 0.35 * up * over, 0, -1.45 * up * over);
      within(hips, 'RightUpLeg', thighRot);
      const thigh = hips.clone().multiply(thighRot);
      const kneeRot = q(2.3 * up * (1 - snap), 0, 0);
      within(thigh, 'RightLeg', kneeRot);
      within(thigh.clone().multiply(kneeRot), 'RightFoot', q(0.6 * up, 0, 0));
      const chest = hips.clone().multiply(q(0, -0.6 * over, 0));
      within(hips, 'Spine', q(-0.1 * up, -0.6 * over, -0.45 * over));
      within(chest, 'Neck', q(0, -0.6 * over - 0.15 * snap, 0.3 * over));
      within(chest, 'LeftArm', q(-0.7 * up, 0, toArm('LeftArm', 0.3 * up + 0.2))); within(chest, 'LeftForeArm', q(-1.9 * up, 0, 0));
      within(chest, 'RightArm', q(0.2 * up + 0.5 * snap, 0, toArm('RightArm', 0.6 * up + 0.2)));
      within(chest, 'RightForeArm', q(-1.4 * up + 0.5 * snap, 0, 0));
      this.fist('Left', up); this.fist('Right', up);
    } else if (clip === 'fall') {
      // Nach hinten umfallen: die Knie geben nach, der Körper kippt mit der Schwerkraft immer schneller (siehe FALL),
      // knickt dabei in der Hüfte, der Kopf zieht zur Brust, die Arme reißen nach hinten und außen. Beim Aufprall
      // federt er kurz zurück, Kopf und Arme schlagen nach, dann liegt er; nach einer Weile steht er wieder auf.
      const c = t % 5;
      const start = 0.25, landed = start + FALL_SECONDS;
      const buckle = ease(c / start);
      const falling = c < start ? 0 : c < landed ? FALL[Math.min(FALL.length - 1, Math.floor((c - start) * 600))] : FALL_ANGLE;
      const after = Math.max(0, c - landed);
      const rebound = c >= landed ? 0.07 * Math.abs(Math.sin(after * Math.PI / 0.16)) * Math.exp(-after * 9) : 0;
      const up = 1 - ease((c - 3.7) / 1.1);
      const f = falling / FALL_ANGLE;
      const hit = c >= landed ? Math.exp(-after * 7) : 0;
      const lying = c >= landed ? ease(after / 0.4) : 0;
      turn('Spine', (0.12 * buckle + 0.25 * f - 0.15 * lying) * up); turn('Neck', (0.15 * buckle + 0.45 * f - 0.5 * hit) * up);
      const arm = (0.25 + 0.9 * f + 0.35 * lying) * up;
      turn('LeftArm', 0.5 * f * (1 - lying) * up, 0, toArm('LeftArm', arm)); turn('RightArm', 0.5 * f * (1 - lying) * up, 0, toArm('RightArm', arm));
      turn('LeftForeArm', -0.4 * f * (1 - lying) * up); turn('RightForeArm', -0.3 * f * (1 - lying) * up);
      // Die Knie geben nach, beim Kippen strecken sich die Beine wieder (wer nach hinten fällt, wird steif), am Boden
      // winkeln sie sich leicht an. Danach liegt der tiefste Knochen so hoch wie im Stand: kein Schweben, kein Einsinken.
      const bend = (0.3 * buckle * (1 - f) + 0.08 * f + 0.25 * lying) * up;
      turn('LeftUpLeg', -0.7 * bend); turn('RightUpLeg', -0.5 * bend); turn('LeftLeg', 1.3 * bend); turn('RightLeg', 0.9 * bend);
      this.tilt(-(falling - rebound) * up);
      this.settle();
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
  // Fingerknochen (Mixamo: Daumen, Zeige- bis kleiner Finger, je drei Glieder) und die Achse, um die jedes Glied zur
  // Faust einrollt. In der Grundhaltung zeigen die Handflächen nach unten; ein Glied rollt also von seiner Richtung
  // nach unten ein. Die Achse wird im Raum der Hand gemerkt, damit sie mitgeht, wenn der Arm sich bewegt.
  private fingers: { bone: THREE.Bone; hand: THREE.Bone; axis: THREE.Vector3; thumb: boolean; side: string }[] = [];

  private measureFingers() {
    this.fingers = [];
    this.root?.updateMatrixWorld(true);
    const down = new THREE.Vector3(0, -1, 0);
    for (const bone of this.bones) {
      const match = /^(Left|Right)Hand(Thumb|Index|Middle|Ring|Pinky)[123]$/.exec(bone.name.replace(/^mixamorig[:_]?/i, ''));
      const hand = match && this.find(`${match[1]}Hand`);
      const child = bone.children.find(c => (c as THREE.Bone).isBone);
      if (!match || !hand || !child) continue;
      const along = child.getWorldPosition(new THREE.Vector3()).sub(bone.getWorldPosition(new THREE.Vector3())).normalize();
      // Der Daumen legt sich quer über die Finger: er dreht zum Mittelfinger hin statt nach unten.
      const middle = this.find(`${match[1]}HandMiddle2`);
      const toward = match[2] === 'Thumb' && middle
        ? middle.getWorldPosition(new THREE.Vector3()).sub(bone.getWorldPosition(new THREE.Vector3())).normalize() : down;
      const axis = along.clone().cross(toward);
      if (axis.lengthSq() < 1e-4) continue;
      axis.normalize().applyQuaternion(hand.getWorldQuaternion(new THREE.Quaternion()).invert());
      this.fingers.push({ bone, hand, axis, thumb: match[2] === 'Thumb', side: match[1] });
    }
    // Vom Handgelenk zu den Spitzen, damit jedes Glied auf dem schon eingerollten aufsetzt.
    const depth = (b: THREE.Object3D): number => (b.parent ? 1 + depth(b.parent) : 0);
    this.fingers.sort((a, b) => depth(a.bone) - depth(b.bone));
  }

  // Die Finger einer Hand zur Faust schließen (amount 0 bis 1); ohne Fingerknochen tut das nichts.
  private fist(side: 'Left' | 'Right', amount: number) {
    for (const { bone, hand, axis, thumb, side: which } of this.fingers) {
      if (which !== side || amount <= 0) continue;
      const world = axis.clone().applyQuaternion(hand.getWorldQuaternion(new THREE.Quaternion()));
      const name = bone.name.replace(/^mixamorig[:_]?/i, '');
      this.turnBy(name, new THREE.Quaternion().setFromAxisAngle(world, amount * (thumb ? 0.7 : 1.35)));
    }
  }

  private lowestBone() {
    this.root?.updateMatrixWorld(true);
    const at = new THREE.Vector3();
    return this.bones.reduce((low, bone) => Math.min(low, bone.getWorldPosition(at).y), Infinity);
  }

  // Die ganze Figur so heben oder senken, dass ihr tiefster Knochen so hoch liegt wie in der Grundhaltung.
  private settle() {
    if (!this.root || !Number.isFinite(this.floor)) return;
    this.root.position.y += this.floor - this.lowestBone();
  }

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
