/**
 * 子供向けにデフォルメした Reachy を three.js で描く。
 *
 * かたちは簡略化しているが、動きは本物のシミュレータの関節角そのもの。
 * サーバの /ws/pose から届く角度（度）を、そのままこのモデルに割り当てている。
 *
 * コンテナの中の RViz は macOS の Docker で GPU が使えず 2〜3fps しか出ないので、
 * 描画だけをブラウザ（Mac の GPU）に移して 60fps を出すのがこのファイルの役目。
 *
 * 腕の寸法は server/table.py と合わせてある（ゲームで手がテーブルの物に届くように、
 * サーバがこの寸法で逆運動学を解いている）。変えるときは両方を直すこと。
 */
import * as THREE from "./vendor/three.module.min.js";

const D2R = Math.PI / 180;

const COLORS = {
  body: 0xf1f3f5,
  bodyDark: 0xced4da,
  stripe: 0x343a40,
  visor: 0x212529,
  eye: 0x4dd4ff,
  antenna: 0xff922b,
  base: 0xadb5bd,
};

// Sim から角度が来ていないときに見せる姿勢（Reachy の default posture に合わせる）
const REST = {
  neck_roll: 0, neck_pitch: -10, neck_yaw: 0,
  l_antenna: 0, r_antenna: 0,
  r_shoulder_pitch: 0, r_shoulder_roll: 10, r_elbow_pitch: 0, r_elbow_yaw: -10,
  l_shoulder_pitch: 0, l_shoulder_roll: -10, l_elbow_pitch: 0, l_elbow_yaw: 10,
  r_gripper: 0, l_gripper: 0,
};

// 肘から、指のあいだ（物をつかむ位置）までの長さ。server/table.py の _FORE と同じ。
const GRASP = 0.67;

// カメラの位置。ゲームではテーブルが見えるよう、上から見下ろす。
const VIEWS = {
  chat: { dist: 4.0, height: 0.75, look: [0, 0.35, 0] },
  game: { dist: 2.9, height: 2.3, look: [0, 0.3, 0.45] },
};

function material(color, opts = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.05, ...opts });
}

/** ひとつの腕（肩→上腕→肘→前腕→手）を組み立てる。side: +1 が Reachy の左。 */
function buildArm(side) {
  const shoulder = new THREE.Group();
  shoulder.position.set(side * 0.42, 0.62, 0);

  const ball = new THREE.Mesh(new THREE.SphereGeometry(0.15, 24, 16), material(COLORS.bodyDark));
  shoulder.add(ball);

  const upper = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.1, 0.42, 8, 16), material(COLORS.body));
  upper.position.y = -0.31;
  shoulder.add(upper);

  // 肘から先は別グループにして、前腕だけを曲げられるようにする
  const elbow = new THREE.Group();
  elbow.position.y = -0.56;
  shoulder.add(elbow);

  const elbowBall = new THREE.Mesh(
    new THREE.SphereGeometry(0.115, 20, 14), material(COLORS.bodyDark));
  elbow.add(elbowBall);

  const fore = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.085, 0.38, 8, 16), material(COLORS.body));
  fore.position.y = -0.29;
  elbow.add(fore);

  // 手首と、2 本指のグリッパー。指の開きは Sim のグリッパーの値で動く。
  const palm = new THREE.Mesh(
    new THREE.BoxGeometry(0.2, 0.08, 0.13), material(COLORS.antenna));
  palm.position.y = -0.56;
  elbow.add(palm);

  const fingers = [-1, 1].map((sign) => {
    const finger = new THREE.Mesh(
      new THREE.BoxGeometry(0.035, 0.16, 0.07), material(COLORS.bodyDark));
    finger.position.set(sign * 0.022, -0.68, 0);
    finger.userData.sign = sign;
    elbow.add(finger);
    return finger;
  });

  // 物をつかむ位置。ゲームでは持った物をここにつける。
  const grasp = new THREE.Object3D();
  grasp.position.y = -GRASP;
  elbow.add(grasp);

  // 本物の腕と同じく、肘は yaw（上腕の軸まわりのひねり）→ pitch（曲げ）の順に回す。
  elbow.rotation.order = "YXZ";

  return { shoulder, elbow, fingers, grasp };
}

/** グリッパーの開き（0〜100%）を指の位置にする。75% で指がちょうど物にふれる。 */
function setFingers(fingers, opening) {
  const x = 0.022 + 0.1 * Math.max(0, Math.min(100, opening)) / 100;
  for (const f of fingers) f.position.x = f.userData.sign * x;
}

function buildAntenna(side) {
  const group = new THREE.Group();
  group.position.set(side * 0.17, 0.3, 0);

  const rod = new THREE.Mesh(
    new THREE.CylinderGeometry(0.016, 0.016, 0.34, 10), material(COLORS.bodyDark));
  rod.position.y = 0.17;
  group.add(rod);

  const tip = new THREE.Mesh(
    new THREE.SphereGeometry(0.06, 18, 12),
    material(COLORS.antenna, { emissive: 0x662200, emissiveIntensity: 0.5 }));
  tip.position.y = 0.36;
  group.add(tip);
  return group;
}

function buildHead() {
  const head = new THREE.Group();
  head.position.y = 1.02;

  const shell = new THREE.Mesh(
    new THREE.BoxGeometry(0.62, 0.5, 0.5), material(COLORS.body));
  head.add(shell);

  // 顔（バイザー）は少し前に出して、目が埋まらないようにする
  const visor = new THREE.Mesh(
    new THREE.BoxGeometry(0.5, 0.26, 0.06),
    material(COLORS.visor, { roughness: 0.3 }));
  visor.position.set(0, 0.02, 0.26);
  head.add(visor);

  for (const x of [-0.11, 0.11]) {
    const eye = new THREE.Mesh(
      new THREE.SphereGeometry(0.062, 20, 14),
      new THREE.MeshStandardMaterial({
        color: COLORS.eye, emissive: COLORS.eye, emissiveIntensity: 0.9, roughness: 0.2,
      }));
    eye.position.set(x, 0.02, 0.29);
    eye.scale.z = 0.5;
    head.add(eye);
  }

  const left = buildAntenna(1), right = buildAntenna(-1);
  head.add(left, right);
  return { head, leftAntenna: left, rightAntenna: right };
}

function buildBody() {
  const body = new THREE.Group();

  const base = new THREE.Mesh(
    new THREE.CylinderGeometry(0.52, 0.58, 0.24, 32), material(COLORS.base));
  base.position.y = -0.72;
  body.add(base);

  const post = new THREE.Mesh(
    new THREE.CylinderGeometry(0.1, 0.12, 0.62, 16), material(COLORS.bodyDark));
  post.position.y = -0.3;
  body.add(post);

  const torso = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.3, 0.42, 10, 24), material(COLORS.body));
  torso.position.y = 0.35;
  torso.scale.z = 0.72;
  body.add(torso);

  // 首。これがないと頭が胴から浮いて見える
  const neck = new THREE.Mesh(
    new THREE.CylinderGeometry(0.09, 0.11, 0.2, 16), material(COLORS.bodyDark));
  neck.position.y = 0.8;
  body.add(neck);

  // 実機のボーダー柄をイメージした帯
  for (let i = 0; i < 3; i++) {
    const stripe = new THREE.Mesh(
      new THREE.TorusGeometry(0.29, 0.028, 8, 28), material(COLORS.stripe));
    stripe.rotation.x = Math.PI / 2;
    stripe.position.y = 0.2 + i * 0.14;
    stripe.scale.z = 0.72;
    body.add(stripe);
  }
  return body;
}

export function createReachy(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);

  const root = new THREE.Group();
  scene.add(root);

  const body = buildBody();
  const { head, leftAntenna, rightAntenna } = buildHead();
  const rArm = buildArm(-1);   // Reachy の右腕は画面の左側
  const lArm = buildArm(1);
  root.add(body, head, rArm.shoulder, lArm.shoulder);

  scene.add(new THREE.HemisphereLight(0xffffff, 0x9a7b5a, 2.0));
  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(2, 4, 3);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffd8a8, 0.6);
  fill.position.set(-3, 1, 2);
  scene.add(fill);

  // 目標角度と表示角度を分けて、届いた値へなめらかに追いつかせる
  const target = { ...REST };
  const shown = { ...REST };
  let live = false;           // Sim から角度が届いているか

  const view = { ...VIEWS.chat, look: [...VIEWS.chat.look] };
  let viewName = "chat";
  const frameHooks = [];

  let azimuth = 0, dragging = false, lastX = 0;
  canvas.style.touchAction = "none";
  canvas.addEventListener("pointerdown", (e) => {
    dragging = true; lastX = e.clientX; canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    azimuth = Math.max(-1.2, Math.min(1.2, azimuth + (e.clientX - lastX) * 0.008));
    lastX = e.clientX;
  });
  const endDrag = () => { dragging = false; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== w * renderer.getPixelRatio() || camera.aspect !== w / h) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
  }

  let t = 0;
  let last = performance.now();
  function frame(now) {
    requestAnimationFrame(frame);
    resize();
    const dt = Math.min(0.05, ((now ?? performance.now()) - last) / 1000);
    last = now ?? performance.now();
    t += 0.016;

    // 1 フレームあたり 18% ずつ近づける。30Hz の更新でもカクつかない。
    for (const k in target) shown[k] += (target[k] - shown[k]) * 0.18;

    head.rotation.set(shown.neck_pitch * D2R, shown.neck_yaw * D2R, shown.neck_roll * D2R);
    leftAntenna.rotation.z = -shown.l_antenna * D2R;
    rightAntenna.rotation.z = -shown.r_antenna * D2R;

    rArm.shoulder.rotation.set(shown.r_shoulder_pitch * D2R, 0, shown.r_shoulder_roll * D2R);
    rArm.elbow.rotation.set(shown.r_elbow_pitch * D2R, shown.r_elbow_yaw * D2R, 0);
    lArm.shoulder.rotation.set(shown.l_shoulder_pitch * D2R, 0, shown.l_shoulder_roll * D2R);
    lArm.elbow.rotation.set(shown.l_elbow_pitch * D2R, shown.l_elbow_yaw * D2R, 0);
    setFingers(rArm.fingers, shown.r_gripper);
    setFingers(lArm.fingers, shown.l_gripper);

    // ほんの少し上下させて、止まっていても「生きている」感じにする。
    // ゲーム中は手先がテーブルの物からずれないよう止める。
    root.position.y = viewName === "game" ? 0 : Math.sin(t * 1.6) * 0.012;

    // カメラは、おしゃべりとゲームで位置を変える。切り替えはなめらかに。
    const want = VIEWS[viewName];
    // 縦長の画面ではテーブルが左右にはみ出すので、そのぶん離れる
    const dist = viewName === "game"
      ? Math.max(want.dist, 1.45 / (Math.tan(19 * D2R) * camera.aspect))
      : want.dist;
    view.dist += (dist - view.dist) * 0.08;
    view.height += (want.height - view.height) * 0.08;
    for (let i = 0; i < 3; i++) view.look[i] += (want.look[i] - view.look[i]) * 0.08;
    camera.position.set(
      view.look[0] + Math.sin(azimuth) * view.dist,
      view.height,
      view.look[2] + Math.cos(azimuth) * view.dist);
    camera.lookAt(...view.look);

    for (const hook of frameHooks) hook(dt);
    renderer.render(scene, camera);
  }
  frame();

  return {
    /** サーバから届いた関節角（度）を反映する。空なら既定の姿勢に戻す。 */
    setPose(pose) {
      live = !!(pose && Object.keys(pose).length);
      const src = live ? pose : REST;
      for (const k in target) if (k in src) target[k] = src[k];
    },
    /** "chat" か "game"。カメラの位置が変わる。 */
    setView(name) { viewName = name in VIEWS ? name : "chat"; },
    /** Sim から角度が届いているか。届いていないと腕は動かない。 */
    isLive() { return live; },
    /** 毎フレーム呼ぶ処理を足す（経過秒が渡る）。 */
    onFrame(hook) { frameHooks.push(hook); },
    scene,
    /** 物をつかむ位置。持った物はここにつける。 */
    hands: { r: rArm.grasp, l: lArm.grasp },
  };
}
