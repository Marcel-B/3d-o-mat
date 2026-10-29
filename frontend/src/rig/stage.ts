// Die Bühne für ein GLB mit Skelett: Szene, Kamera, Proben (Gehen, Schlag, Tritt, Arme heben, Kniebeuge, Umfallen)
// und Knochen von
// Hand. Dieselbe Logik wie der Rig-Prüfstand: die Proben drehen um die Achsen der Szene, nicht der Knochen, und
// gehen von der Grundhaltung der Figur aus (T- oder A-Pose), die beim Laden gemessen wird.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export type Clip = 'walk' | 'punch' | 'kick' | 'arms' | 'squat' | 'fall';
export type Facts = { bones: number; meshes: number; vertices: number; core: { name: string; found: boolean }[] };

// Mixamo-Namen, wie skintokens sie mit --class humanoid vergibt (mit oder ohne "mixamorig:").
const CORE = ['Hips', 'Spine', 'Spine1', 'Spine2', 'Neck', 'Head', 'LeftShoulder', 'LeftArm', 'LeftForeArm', 'LeftHand',
  'RightShoulder', 'RightArm', 'RightForeArm', 'RightHand', 'LeftUpLeg', 'LeftLeg', 'LeftFoot', 'RightUpLeg', 'RightLeg', 'RightFoot'];

// 0 bis 1 weich, außerhalb festgehalten.
const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

export class RigStage {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
  private controls: OrbitControls;
  private root: THREE.Object3D | null = null;
  private helper: THREE.SkeletonHelper | null = null;
  private meshes: THREE.SkinnedMesh[] = [];
  private rest = new Map<THREE.Bone, THREE.Quaternion>();
  // Die Lage der ganzen Figur nach dem Einpassen; Umfallen kippt sie um die Fersen und stellt sie danach zurück.
  private rootPosition = new THREE.Vector3();
  private rootQuaternion = new THREE.Quaternion();
  private heel = 0;
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
    this.bones = []; this.meshes = []; this.rest.clear();
    this.root.traverse(object => {
      if ((object as THREE.Bone).isBone) this.bones.push(object as THREE.Bone);
      if ((object as THREE.SkinnedMesh).isSkinnedMesh) this.meshes.push(object as THREE.SkinnedMesh);
    });
    this.bones.forEach(bone => this.rest.set(bone, bone.quaternion.clone()));
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

  private toArm(arm: string, angle: number) {
    return (Math.sign(this.armAngle[arm]) || (arm === 'LeftArm' ? 1 : -1)) * angle - this.armAngle[arm];
  }

  // Die Figur schaut nach +Z (so liefert TRELLIS.2 sie). X: vor/zurück (positiv kippt nach vorn, was nach oben zeigt,
  // und nach hinten, was nach unten hängt), Y: um die Hochachse, Z: seitwärts.
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
      const s = Math.sin(t * 5);
      turn('Spine', 0, 0.12 * s);
      turn('LeftArm', 0.45 * s, 0, toArm('LeftArm', 0.2)); turn('RightArm', -0.45 * s, 0, toArm('RightArm', 0.2));
      turn('LeftForeArm', -0.35); turn('RightForeArm', -0.35);
      turn('LeftUpLeg', -0.5 * s); turn('RightUpLeg', 0.5 * s);
      turn('LeftLeg', 0.9 * Math.max(0, Math.sin(t * 5 + 1.8))); turn('RightLeg', 0.9 * Math.max(0, -Math.sin(t * 5 + 1.8)));
    } else if (clip === 'punch') {
      const p = Math.max(0, Math.sin(t * 4)) ** 2;
      turn('Spine', 0, 0.35 * p); turn('Spine1', 0, 0.15 * p);
      turn('RightArm', 0, 1.5, toArm('RightArm', Math.PI / 2)); turn('RightForeArm', 0, 1.6 * (1 - p));
      turn('LeftArm', 0, -1.3, toArm('LeftArm', Math.PI / 2 - 0.2)); turn('LeftForeArm', 0, -1.7);
    } else if (clip === 'kick') {
      // Knie anziehen, Unterschenkel vorschnellen, zurück; das Standbein gibt etwas nach, die Arme halten Balance.
      const c = (t % 1.8) / 1.8;
      const up = ease(c / 0.3) * (1 - ease((c - 0.62) / 0.3));
      const snap = ease((c - 0.3) / 0.1) * (1 - ease((c - 0.48) / 0.14));
      turn('Spine', -0.25 * up);
      turn('RightUpLeg', -1.45 * up); turn('RightLeg', 1.9 * up * (1 - snap)); turn('RightFoot', 0.5 * snap);
      turn('LeftLeg', 0.2 * up); turn('LeftUpLeg', -0.1 * up);
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

  setClip(clip: Clip | null) {
    this.resetPose();
    this.clip = clip;
    this.started = performance.now();
  }

  resetPose() {
    this.bones.forEach(bone => bone.quaternion.copy(this.rest.get(bone)!));
    this.root?.position.copy(this.rootPosition);
    this.root?.quaternion.copy(this.rootQuaternion);
  }

  // Drehung eines Knochens um seine eigenen Achsen, in Grad, gegenüber der Grundhaltung.
  rotate(index: number, degrees: [number, number, number]) {
    const bone = this.bones[index];
    if (!bone) return;
    this.clip = null;
    const [x, y, z] = degrees.map(THREE.MathUtils.degToRad);
    bone.quaternion.copy(this.rest.get(bone)!).multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z)));
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
