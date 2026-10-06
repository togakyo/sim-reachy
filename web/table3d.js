/**
 * おかたづけゲームのテーブルと、その上の物を描く。
 *
 * 置き場所はサーバ (server/table.py) が決めて world として送ってくる。
 * ここではそれを描き、物が動いたときの見せ方（つかむ・入れる・落ちる・ころがる）を付ける。
 * つかんだ物は Reachy の手（グリッパー）につけるので、腕の動き（Sim の関節角）と一緒に動く。
 */
import * as THREE from "./vendor/three.module.min.js";

const COLORS = { red: 0xff6b6b, blue: 0x4dabf7, yellow: 0xffd43b, green: 0x51cf66 };
const WOOD = 0xe9c79a;
const BOX = 0xd6a46b;
const BASKET = 0xf4a3b4;
const FLOOR_Y = -0.84;   // Reachy の台座の下

function material(color, opts = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.02, ...opts });
}

function v3(a) { return new THREE.Vector3(a[0], a[1], a[2]); }

/** 入れ物の前に出す名前の札。 */
function makeLabel(text) {
  const canvas = document.createElement("canvas");
  canvas.width = 256; canvas.height = 128;
  const g = canvas.getContext("2d");
  g.fillStyle = "rgba(255,255,255,0.92)";
  g.beginPath();
  g.roundRect(8, 16, 240, 96, 40);
  g.fill();
  g.fillStyle = "#5c3d1e";
  g.font = 'bold 64px "Hiragino Maru Gothic ProN", "Hiragino Sans", sans-serif';
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(text, 128, 66);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(canvas), depthTest: false, transparent: true,
  }));
  sprite.scale.set(0.3, 0.15, 1);
  sprite.renderOrder = 10;
  return sprite;
}

function buildTable(t) {
  const group = new THREE.Group();
  const width = t.half_width * 2, depth = t.front - t.back;
  const top = new THREE.Mesh(new THREE.BoxGeometry(width, 0.06, depth), material(WOOD));
  top.position.set(0, t.y - 0.03, (t.front + t.back) / 2);
  group.add(top);
  const legH = t.y - 0.06 - FLOOR_Y;
  for (const x of [-t.half_width + 0.08, t.half_width - 0.08]) {
    for (const z of [t.back + 0.08, t.front - 0.08]) {
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, legH, 12), material(0xc9a27a));
      leg.position.set(x, FLOOR_Y + legH / 2, z);
      group.add(leg);
    }
  }
  return group;
}

/** はこ: 上があいた箱。 */
function buildBox(height) {
  const group = new THREE.Group();
  const w = 0.34, th = 0.02;
  const mat = material(BOX);
  const bottom = new THREE.Mesh(new THREE.BoxGeometry(w, th, w), mat);
  bottom.position.y = th / 2;
  group.add(bottom);
  for (const [x, z, sx, sz] of [[0, w / 2, w, th], [0, -w / 2, w, th], [w / 2, 0, th, w], [-w / 2, 0, th, w]]) {
    const wall = new THREE.Mesh(new THREE.BoxGeometry(sx, height, sz), mat);
    wall.position.set(x, height / 2, z);
    group.add(wall);
  }
  return group;
}

/** かご: 上があいた丸いかご。 */
function buildBasket(height) {
  const group = new THREE.Group();
  const mat = material(BASKET, { side: THREE.DoubleSide });
  const wall = new THREE.Mesh(new THREE.CylinderGeometry(0.19, 0.15, height, 28, 1, true), mat);
  wall.position.y = height / 2;
  group.add(wall);
  const bottom = new THREE.Mesh(new THREE.CircleGeometry(0.15, 28), mat);
  bottom.rotation.x = -Math.PI / 2;
  bottom.position.y = 0.01;
  group.add(bottom);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(0.19, 0.018, 8, 32), material(0xe8899c));
  rim.rotation.x = Math.PI / 2;
  rim.position.y = height;
  group.add(rim);
  return group;
}

function buildThing(obj, r) {
  const mat = material(COLORS[obj.color] ?? 0xadb5bd, { roughness: 0.45 });
  const geo = obj.kind === "ball"
    ? new THREE.SphereGeometry(r, 28, 20)
    : new THREE.BoxGeometry(r * 1.9, r * 1.9, r * 1.9);
  return new THREE.Mesh(geo, mat);
}

const ease = (t) => 1 - Math.pow(1 - t, 3);

export function createTable(reachy) {
  const group = new THREE.Group();
  group.visible = false;
  reachy.scene.add(group);

  let world = null;
  let built = false;
  const things = new Map();   // id → { mesh, where, anim }
  const bins = {};            // name → { pos }
  // Sim につながっていないと腕が動かないので、持った物は置き場所の上に浮かせて見せる
  const floatAt = { r: null, l: null };

  function build(w) {
    group.add(buildTable(w.table));
    for (const [name, b] of Object.entries(w.bins)) {
      const mesh = name === "box" ? buildBox(w.bin_height) : buildBasket(w.bin_height);
      mesh.position.copy(v3(b.pos));
      group.add(mesh);
      const label = makeLabel(b.label);
      label.position.set(b.pos[0], b.pos[1] + w.bin_height + 0.13, b.pos[2] + 0.12);
      group.add(label);
      bins[name] = { pos: v3(b.pos) };
    }
    built = true;
  }

  /** 入れ物の中で、n 番目に入った物の位置。2 つずつ重ねて入れる。 */
  function binSpot(name, index) {
    const base = bins[name].pos.clone();
    const r = world.obj_radius;
    const layer = Math.floor(index / 2), col = index % 2;
    return base.add(new THREE.Vector3(col ? 0.06 : -0.06, 0.02 + r + layer * r * 1.7, col ? 0.04 : -0.04));
  }

  /** 物を置く位置（world 座標）。手の中なら null。 */
  function restingSpot(obj) {
    if (obj.where in world.slots) return v3(world.slots[obj.where]);
    if (obj.where in world.bins) return binSpot(obj.where, world.bins[obj.where].items.indexOf(obj.id));
    return null;
  }

  function handSide(where) { return where.startsWith("hand_") ? where.slice(5) : null; }

  function worldPos(mesh) { return mesh.getWorldPosition(new THREE.Vector3()); }

  /** 物を手から離して、テーブルの座標に戻す（見た目の位置はそのまま）。 */
  function detach(mesh) {
    if (mesh.parent !== group) group.attach(mesh);
  }

  function setWorld(w, cause) {
    const reset = !built || cause?.how === "reset";
    world = w;
    if (!built) build(w);

    const ids = new Set(w.objects.map((o) => o.id));
    for (const [id, th] of things) {
      if (!ids.has(id)) { th.mesh.removeFromParent(); things.delete(id); }
    }
    for (const obj of w.objects) {
      let th = things.get(obj.id);
      if (!th) {
        th = { mesh: buildThing(obj, w.obj_radius), where: null, anim: null };
        group.add(th.mesh);
        things.set(obj.id, th);
      }
      const moved = th.where !== obj.where;
      const how = cause && cause.obj === obj.id ? cause.how : null;
      if (reset) {
        place(th, obj, "pop");
      } else if (how === "show" && !reachy.isLive()) {
        floatTo(th, obj, new THREE.Vector3(world.slots.r1[0] * 0.4, 0.55, 1.0));
      } else if (moved || how) {
        place(th, obj, how);
      }
      th.where = obj.where;
    }
  }

  /** 物を新しい置き場所へ。how に応じて動きを付ける。 */
  function place(th, obj, how) {
    const mesh = th.mesh;
    const side = handSide(obj.where);
    const live = reachy.isLive();

    if (side) {
      const from = worldPos(mesh);
      if (live) {
        // 手にくっつけて、指のあいだへ吸いよせる
        reachy.hands[side].attach(mesh);
        th.anim = { type: "toHand", t: 0, dur: 0.18, from: mesh.position.clone() };
      } else {
        detach(mesh);
        floatAt[side] = from.clone().add(new THREE.Vector3(0, 0.35, 0));
        th.anim = { type: "hop", t: 0, dur: 0.5, from, to: floatAt[side].clone() };
      }
      return;
    }

    const to = restingSpot(obj);
    const from = worldPos(mesh);
    detach(mesh);
    mesh.scale.setScalar(1);
    if (how === "pop") {
      mesh.position.copy(to);
      mesh.rotation.set(0, 0, 0);
      th.anim = { type: "pop", t: 0, dur: 0.35 };
    } else if (how === "roll" && live) {
      const via = cause_via(obj) ?? to;
      th.anim = { type: "fall", t: 0, dur: 0.35, from, to: via, then: { type: "roll", dur: 0.9, from: via, to } };
    } else if ((how === "drop" || how === "place" || how === "roll") && live) {
      th.anim = { type: "fall", t: 0, dur: 0.38, from, to };
    } else if (how === "putdown" && live) {
      th.anim = { type: "slide", t: 0, dur: 0.25, from, to };
    } else {
      // Sim につながっていないときや、理由の分からない移動は、ぴょんと飛ばす
      th.anim = { type: "hop", t: 0, dur: 0.55, from, to };
    }
  }

  // roll のときにボールが最初に落ちる置き場所。setWorld が受け取った cause から引く。
  let lastCause = null;
  function cause_via(obj) {
    const via = lastCause && lastCause.obj === obj.id ? lastCause.via : null;
    return via && world.slots[via] ? v3(world.slots[via]) : null;
  }

  function floatTo(th, obj, pos) {
    const side = handSide(obj.where);
    const from = worldPos(th.mesh);
    detach(th.mesh);
    if (side) floatAt[side] = pos.clone();
    th.anim = { type: "hop", t: 0, dur: 0.5, from, to: pos };
  }

  function step(th, dt) {
    const a = th.anim;
    if (!a) return;
    a.t = Math.min(1, a.t + dt / a.dur);
    const m = th.mesh;
    const t = a.t;
    if (a.type === "toHand") {
      m.position.lerpVectors(a.from, new THREE.Vector3(), ease(t));
    } else if (a.type === "pop") {
      const s = t < 0.7 ? ease(t / 0.7) * 1.15 : 1.15 - 0.15 * ((t - 0.7) / 0.3);
      m.scale.setScalar(Math.max(0.01, s));
    } else if (a.type === "hop") {
      m.position.lerpVectors(a.from, a.to, ease(t));
      m.position.y += Math.sin(Math.PI * t) * 0.3;
    } else if (a.type === "slide") {
      m.position.lerpVectors(a.from, a.to, ease(t));
    } else if (a.type === "fall") {
      // 落ちる（だんだん速く）→ 小さくはねる
      if (t < 0.75) {
        const k = t / 0.75;
        m.position.set(
          a.from.x + (a.to.x - a.from.x) * k,
          a.from.y + (a.to.y - a.from.y) * k * k,
          a.from.z + (a.to.z - a.from.z) * k);
      } else {
        m.position.copy(a.to);
        m.position.y += Math.sin(Math.PI * (t - 0.75) / 0.25) * 0.04;
      }
    } else if (a.type === "roll") {
      const k = ease(t);
      const prev = m.position.clone();
      m.position.lerpVectors(a.from, a.to, k);
      // 進んだぶんだけ回す（すべらずにころがって見えるように）
      const d = m.position.clone().sub(prev);
      const len = d.length();
      if (len > 1e-6) {
        const axis = new THREE.Vector3(d.z, 0, -d.x).normalize();
        m.rotateOnWorldAxis(axis, len / world.obj_radius);
      }
    }
    if (a.t >= 1) {
      th.anim = a.then ? { ...a.then, t: 0 } : null;
    }
  }

  reachy.onFrame((dt) => {
    if (!group.visible) return;
    for (const th of things.values()) step(th, dt);
  });

  return {
    /** サーバから届いたテーブルのようす（と、物が動いた理由）を反映する。 */
    setWorld(w, cause) {
      lastCause = cause;
      setWorld(w, cause);
    },
    setVisible(on) { group.visible = on; },
  };
}
