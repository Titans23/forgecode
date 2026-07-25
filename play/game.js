const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
const statusEl = document.getElementById('status');
const logEl = document.getElementById('log');
const cardsEl = document.getElementById('cards');
const pauseBtn = document.getElementById('pauseBtn');
const restartBtn = document.getElementById('restartBtn');
const eraseBtn = document.getElementById('eraseBtn');
const clearBtn = document.getElementById('clearBtn');
const burstBtn = document.getElementById('burstBtn');

const COLS = 9;
const ROWS = 5;
const BOARD = { x: 18, y: 52, w: 880, h: 450 };
const CW = BOARD.w / COLS;
const CH = BOARD.h / ROWS;
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
const rnd = (a, b) => Math.random() * (b - a) + a;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

const PLANTS = [
  { id: 'pea', name: '豌豆射手', cost: 100, cd: 4, hp: 160, tip: '持续输出', c: '#74d86c' },
  { id: 'sunflower', name: '向日葵', cost: 50, cd: 6, hp: 110, tip: '资源引擎', c: '#f2c94c' },
  { id: 'wallnut', name: '坚果墙', cost: 75, cd: 12, hp: 720, tip: '高血量前排', c: '#bb8442' },
  { id: 'icepea', name: '寒冰射手', cost: 175, cd: 7, hp: 150, tip: '减速控场', c: '#8edfff' },
  { id: 'repeater', name: '双发射手', cost: 200, cd: 9, hp: 150, tip: '双连射压制', c: '#89e279' },
  { id: 'cherry', name: '樱桃炸弹', cost: 150, cd: 18, hp: 80, tip: '延迟爆破', c: '#ff7e88' },
];

const ZOMBIES = {
  normal: { n: '普通僵尸', hp: 120, sp: 15, dmg: 16, ar: 0, w: 56, h: 78, c: '#9ccf7c', pts: 10 },
  cone: { n: '路障僵尸', hp: 250, sp: 13, dmg: 18, ar: 45, w: 58, h: 82, c: '#86b56c', pts: 14 },
  bucket: { n: '铁桶僵尸', hp: 440, sp: 10, dmg: 22, ar: 140, w: 60, h: 84, c: '#85ad6a', pts: 20 },
  runner: { n: '撑杆僵尸', hp: 95, sp: 30, dmg: 14, ar: 0, w: 54, h: 78, c: '#a8dc86', pts: 16 },
  brute: { n: '巨盾僵尸', hp: 820, sp: 8, dmg: 30, ar: 240, w: 72, h: 92, c: '#6f9b57', pts: 32 },
};

const S = {
  t: 0,
  sun: 150,
  score: 0,
  wave: 0,
  lives: 10,
  paused: false,
  over: false,
  tool: 'plant',
  pick: 'pea',
  burst: 20,
  burstCd: 0,
  waveText: '准备中',
  queue: [],
  qTimer: 0,
  nextWave: 2.5,
  sky: 3.5,
  hover: null,
  flash: 0,
  cool: new Map(),
  logs: [],
  pulse: 0,
};

let plants = [];
let zombies = [];
let shots = [];
let suns = [];
let particles = [];
let floaters = [];
let last = performance.now();

const log = (msg, tone = '') => {
  S.logs.unshift({ t: new Date().toLocaleTimeString('zh-CN', { hour12: false }), msg, tone });
  S.logs = S.logs.slice(0, 12);
};
const fxText = (x, y, text, color = '#fff') => floaters.push({ x, y, text, color, life: 1, vy: -28 });
const emit = (x, y, color, n = 8, spread = 56) => {
  for (let i = 0; i < n; i++) particles.push({ x, y, vx: rnd(-spread, spread), vy: rnd(-spread, spread), life: rnd(0.35, 0.8), color, size: rnd(2, 4) });
};
const cellCenter = (r, c) => ({ x: BOARD.x + c * CW + CW / 2, y: BOARD.y + r * CH + CH / 2 });
const cellFromPoint = (x, y) => (x < BOARD.x || x > BOARD.x + BOARD.w || y < BOARD.y || y > BOARD.y + BOARD.h ? null : { row: Math.floor((y - BOARD.y) / CH), col: Math.floor((x - BOARD.x) / CW) });
const getPlant = (r, c) => plants.find(p => p.row === r && p.col === c);
const readyAt = id => S.cool.get(id) || 0;
const cdLeft = id => Math.max(0, readyAt(id) - S.t);
const addCd = (id, sec) => S.cool.set(id, S.t + sec);

function spawnSun(x, y, value = 25, drifting = true) {
  suns.push({ x, y, value, drifting, life: 10, vy: drifting ? -10 : rnd(28, 44), r: 15, collected: false });
}
function collectSun(s) {
  if (s.collected) return;
  s.collected = true;
  S.sun += s.value;
  S.score += 1;
  S.burst = clamp(S.burst + 7, 0, 100);
  fxText(s.x, s.y, `+${s.value}`, '#ffe98a');
  emit(s.x, s.y, '#ffe98a', 10, 48);
  log(`收集到 ${s.value} 阳光。`, 'good');
}

function buildCards() {
  cardsEl.innerHTML = '';
  for (const p of PLANTS) {
    const b = document.createElement('button');
    b.className = 'plant-card';
    b.dataset.id = p.id;
    b.innerHTML = `<div class="card-head"><strong>${p.name}</strong><span>${p.cost} ☀</span></div><div class="card-desc">${p.tip}</div><div class="card-foot"><span>CD ${p.cd}s</span><span>HP ${p.hp}</span></div>`;
    b.addEventListener('click', () => {
      S.tool = 'plant';
      S.pick = p.id;
      eraseBtn.classList.remove('active');
      reflow();
    });
    cardsEl.appendChild(b);
  }
}

function makePlant(def, row, col) {
  const c = cellCenter(row, col);
  return { id: uid(), def, row, col, x: c.x, y: c.y, hp: def.hp, maxHp: def.hp, age: 0, cd: rnd(0.2, 0.7), fuse: def.id === 'cherry' ? 1.1 : 0, shake: 0, dead: false };
}
function makeZombie(type, lane) {
  const z = ZOMBIES[type];
  return { id: uid(), type, lane, x: BOARD.x + BOARD.w + rnd(24, 80), y: BOARD.y + lane * CH + CH / 2, hp: z.hp, maxHp: z.hp, armor: z.ar, speed: z.sp, damage: z.dmg, width: z.w, height: z.h, color: z.c, bite: 0, slow: 0, dead: false, passed: false };
}

function damageZombie(z, amount, kind = 'normal') {
  if (z.dead) return;
  let d = amount;
  if (z.armor > 0) {
    const abs = Math.min(z.armor, d);
    z.armor -= abs;
    d -= abs;
  }
  if (d > 0) z.hp -= d;
  if (kind === 'ice') z.slow = Math.max(z.slow, 2.6);
  if (kind === 'burst') z.slow = Math.max(z.slow, 1.4);
  if (z.hp <= 0) {
    z.dead = true;
    const pts = ZOMBIES[z.type].pts;
    S.score += pts;
    S.burst = clamp(S.burst + 3, 0, 100);
    fxText(z.x, z.y - 34, `+${pts}`, '#b8ff94');
    emit(z.x, z.y - 10, '#b8ff94', 14, 80);
    if (Math.random() < 0.32) spawnSun(z.x + rnd(-8, 8), z.y - 24, 25, false);
  }
}
function explodeAt(x, y, radius, damage) {
  emit(x, y, '#ffb3a7', 24, 120);
  for (const z of zombies) {
    const d = Math.hypot(z.x - x, z.y - y);
    if (d <= radius) damageZombie(z, damage + Math.floor((radius - d) / 18), 'burst');
  }
}
function spawnZombie(type, lane) {
  zombies.push(makeZombie(type, lane));
  emit(BOARD.x + BOARD.w, BOARD.y + lane * CH + CH / 2, '#b9f3a7', 6, 24);
}
function shoot(plant, speed, damage, color, slow = 0, yOff = -8) {
  shots.push({ x: plant.x + 18, y: plant.y + yOff, lane: plant.row, speed, damage, color, slow, life: 5, r: 6 });
}

function buildQueue(wave) {
  const q = [];
  const total = 5 + wave * 2 + (wave % 5 === 0 ? 3 : 0);
  for (let i = 0; i < total; i++) {
    const r = Math.random();
    let type = 'normal';
    if (wave >= 7 && r > 0.83) type = 'brute';
    else if (wave >= 5 && r > 0.67) type = 'bucket';
    else if (wave >= 3 && r > 0.5) type = 'cone';
    else if (wave >= 4 && r > 0.83) type = 'runner';
    q.push({ type, lane: Math.floor(rnd(0, ROWS)), delay: i === 0 ? 0.2 : rnd(0.55, 1.4) });
  }
  return q;
}
function startWave(wave) {
  S.wave = wave;
  S.queue = buildQueue(wave);
  S.qTimer = 0.4;
  S.nextWave = 0;
  S.waveText = `第 ${wave} 波`;
  log(`第 ${wave} 波来袭，注意阵型与资源。`, 'warn');
  if (wave % 5 === 0) log('警报：本轮会出现更强敌人。', 'bad');
}
function triggerBurst() {
  if (S.over || S.burst < 100 || S.burstCd > 0) return;
  S.burst = 0;
  S.burstCd = 18;
  S.flash = 0.45;
  log('太阳风暴释放！', 'good');
  for (const z of zombies) damageZombie(z, 72, 'burst');
  for (let r = 0; r < ROWS; r++) {
    const c = cellCenter(r, 4);
    spawnSun(c.x + rnd(-20, 20), c.y - 12, 25, false);
  }
  emit(BOARD.x + BOARD.w / 2, BOARD.y + BOARD.h / 2, '#fff2a4', 50, 180);
}

function placePlant(row, col) {
  const def = PLANTS.find(p => p.id === S.pick);
  if (!def || S.tool !== 'plant' || S.paused || S.over) return;
  if (getPlant(row, col)) return log('该格子已经有植物。', 'bad');
  if (S.sun < def.cost) return log('阳光不足。', 'bad');
  if (cdLeft(def.id) > 0) return log(`${def.name} 正在冷却。`, 'warn');
  S.sun -= def.cost;
  addCd(def.id, def.cd);
  const p = makePlant(def, row, col);
  plants.push(p);
  emit(p.x, p.y, def.c, 14, 46);
  log(`部署 ${def.name} 于 ${row + 1}-${col + 1}。`, 'good');
  if (def.id === 'sunflower') spawnSun(p.x, p.y - 40, 25);
}
function removePlant(row, col) {
  const idx = plants.findIndex(p => p.row === row && p.col === col);
  if (idx >= 0) {
    const [p] = plants.splice(idx, 1);
    emit(p.x, p.y, '#a7d69a', 10, 50);
    log(`移除 ${p.def.name}。`, 'warn');
  }
}
function clearPlants() {
  if (!plants.length) return;
  plants = [];
  emit(BOARD.x + BOARD.w / 2, BOARD.y + BOARD.h / 2, '#f2d27e', 32, 130);
  log('已清空全部植物。', 'warn');
}

function updatePlants(dt) {
  for (const p of plants) {
    p.age += dt;
    if (p.shake > 0) p.shake = Math.max(0, p.shake - dt * 2.6);
    if (p.def.id === 'sunflower') {
      p.cd -= dt;
      if (p.cd <= 0) {
        p.cd = 7;
        spawnSun(p.x + rnd(-16, 16), p.y - 40, 25);
        S.burst = clamp(S.burst + 2, 0, 100);
        fxText(p.x, p.y - 44, '+25', '#ffe98a');
      }
    } else if (p.def.id === 'pea' || p.def.id === 'icepea' || p.def.id === 'repeater') {
      p.cd -= dt;
      const enemy = zombies.find(z => !z.dead && z.lane === p.row && z.x > p.x - 4);
      if (p.cd <= 0 && enemy) {
        if (p.def.id === 'repeater') {
          shoot(p, 310, 18, '#8ee375');
          shoot(p, 310, 18, '#8ee375', 0, -2);
          p.cd = 1.35;
        } else if (p.def.id === 'icepea') {
          shoot(p, 290, 18, '#9bddff', 2.6);
          p.cd = 1.7;
        } else {
          shoot(p, 300, 22, '#8ee375');
          p.cd = 1.85;
        }
      }
    } else if (p.def.id === 'cherry') {
      p.fuse -= dt;
      if (p.fuse <= 0) {
        const n = zombies.filter(z => !z.dead && Math.hypot(z.x - p.x, z.y - p.y) <= 126).length;
        explodeAt(p.x, p.y, 126, 120);
        p.dead = true;
        log(`樱桃炸弹爆炸，清理 ${n} 个目标。`, 'bad');
      }
    }
  }
  plants = plants.filter(p => !p.dead && p.hp > 0);
}
function updateZombies(dt) {
  for (const z of zombies) {
    if (z.dead) continue;
    if (z.slow > 0) z.slow -= dt;
    const mul = z.slow > 0 ? 0.5 : 1;
    const target = plants.filter(p => p.row === z.lane).sort((a, b) => b.x - a.x).find(p => p.x <= z.x + 18);
    if (target && z.x - z.width / 2 <= target.x + 28) {
      z.bite += dt;
      if (z.bite >= 0.52) {
        target.hp -= z.damage * 0.48;
        target.shake = 0.12;
        z.bite = 0;
        emit(target.x - 12, target.y, '#eff7dd', 4, 18);
        if (target.hp <= 0) {
          target.dead = true;
          emit(target.x, target.y, '#f0b27a', 12, 42);
          log(`${target.def.name} 被摧毁。`, 'bad');
        }
      }
    } else {
      z.x -= z.speed * mul * dt;
      z.bite = 0;
    }
    if (z.x < BOARD.x - 36 && !z.passed) {
      z.passed = true;
      z.dead = true;
      S.lives -= 1;
      emit(BOARD.x + 12, z.y, '#ff7f7f', 18, 80);
      log('有僵尸突破防线！', 'bad');
    }
  }
  zombies = zombies.filter(z => !z.dead);
}
function updateShots(dt) {
  for (const b of shots) {
    b.x += b.speed * dt;
    b.life -= dt;
    const hit = zombies.find(z => !z.dead && z.lane === b.lane && b.x >= z.x - z.width * 0.46 && b.x <= z.x + z.width * 0.46 && Math.abs(b.y - z.y) < z.height * 0.3);
    if (hit) {
      damageZombie(hit, b.damage, b.slow > 0 ? 'ice' : 'normal');
      if (b.slow > 0) hit.slow = Math.max(hit.slow, b.slow);
      b.life = 0;
      emit(b.x, b.y, b.color, 5, 26);
    }
  }
  shots = shots.filter(b => b.life > 0 && b.x < BOARD.x + BOARD.w + 60);
}
function updateSuns(dt) {
  for (const s of suns) {
    s.life -= dt;
    if (s.drifting) s.y += Math.sin(S.t * 2 + s.x * 0.01) * dt * 14;
    else s.y += s.vy * dt * 0.35;
  }
  suns = suns.filter(s => s.life > 0 && !s.collected);
}
function updateParticles(dt) {
  for (const p of particles) {
    p.life -= dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.vy += 120 * dt;
  }
  particles = particles.filter(p => p.life > 0);
  for (const f of floaters) {
    f.life -= dt;
    f.y += f.vy * dt;
  }
  floaters = floaters.filter(f => f.life > 0);
}

function updateWave(dt) {
  if (S.wave === 0) {
    S.nextWave -= dt;
    S.waveText = `首波倒计时 ${Math.max(0, S.nextWave).toFixed(1)}s`;
    if (S.nextWave <= 0) startWave(1);
    return;
  }
  if (S.queue.length > 0) {
    S.qTimer -= dt;
    if (S.qTimer <= 0) {
      const n = S.queue.shift();
      spawnZombie(n.type, n.lane);
      S.qTimer = n.delay;
      S.waveText = `第 ${S.wave} 波 · 敌军 ${S.queue.length + zombies.length}`;
    }
    return;
  }
  if (zombies.length === 0) {
    S.nextWave -= dt;
    S.waveText = `第 ${S.wave} 波已清空 · ${Math.max(0, S.nextWave).toFixed(1)}s 后下一波`;
    if (S.nextWave <= 0) {
      S.nextWave = Math.max(5, 10 - S.wave * 0.45);
      startWave(S.wave + 1);
    }
  }
}
function updateCooldowns() {
  for (const [id, t] of S.cool.entries()) if (t <= S.t) S.cool.delete(id);
}

function tick(dt) {
  if (!S.paused && !S.over) {
    S.t += dt;
    S.pulse += dt;
    S.burstCd = Math.max(0, S.burstCd - dt);
    S.sky -= dt;
    if (S.sky <= 0) {
      S.sky = rnd(4.5, 7.5);
      const row = Math.floor(rnd(0, ROWS));
      const c = cellCenter(row, Math.floor(rnd(0, COLS)));
      spawnSun(c.x + rnd(-18, 18), 18, 25, false);
      log('天空落下一颗阳光。', 'good');
    }
    updateWave(dt);
    updatePlants(dt);
    updateZombies(dt);
    updateShots(dt);
    updateSuns(dt);
    updateParticles(dt);
    updateCooldowns();
    if (S.flash > 0) S.flash -= dt;
    if (S.lives <= 0) {
      S.over = true;
      S.paused = true;
      S.waveText = '基地失守';
      log('基地被突破，游戏结束。', 'bad');
    }
  }
  reflow();
  draw();
  requestAnimationFrame(loop);
}

function drawBoard() {
  const g = ctx.createLinearGradient(0, 0, 0, canvas.height);
  g.addColorStop(0, '#1b4329');
  g.addColorStop(0.55, '#1e5634');
  g.addColorStop(1, '#11301d');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = 'rgba(255,255,255,0.05)';
  ctx.fillRect(0, 0, canvas.width, BOARD.y);
  ctx.fillStyle = '#102219';
  ctx.fillRect(0, BOARD.y + BOARD.h, canvas.width, canvas.height - BOARD.y - BOARD.h);
  for (let r = 0; r < ROWS; r++) {
    ctx.fillStyle = r % 2 === 0 ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.05)';
    ctx.fillRect(BOARD.x, BOARD.y + r * CH, BOARD.w, CH);
  }
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  for (let c = 0; c <= COLS; c++) {
    const x = BOARD.x + c * CW;
    ctx.beginPath();
    ctx.moveTo(x, BOARD.y);
    ctx.lineTo(x, BOARD.y + BOARD.h);
    ctx.stroke();
  }
  for (let r = 0; r <= ROWS; r++) {
    const y = BOARD.y + r * CH;
    ctx.beginPath();
    ctx.moveTo(BOARD.x, y);
    ctx.lineTo(BOARD.x + BOARD.w, y);
    ctx.stroke();
  }
  ctx.fillStyle = 'rgba(160,255,170,0.08)';
  for (let i = 0; i < 18; i++) ctx.fillRect((i * 61 + S.pulse * 34) % canvas.width, BOARD.y + (i % ROWS) * CH + 10, 52, 4);
  if (S.hover) {
    const { row, col } = S.hover;
    ctx.fillStyle = S.tool === 'shovel' ? 'rgba(255,160,120,0.18)' : 'rgba(185,255,170,0.16)';
    ctx.fillRect(BOARD.x + col * CW + 2, BOARD.y + row * CH + 2, CW - 4, CH - 4);
  }
  if (S.flash > 0) {
    ctx.fillStyle = `rgba(255,248,208,${S.flash * 0.35})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
}
function drawPlant(p) {
  ctx.save();
  ctx.translate(p.x, p.y + Math.sin(p.age * 4) * 2);
  if (p.shake > 0) ctx.translate(rnd(-3, 3), rnd(-2, 2));
  if (p.def.id === 'sunflower') {
    ctx.fillStyle = '#5a9447';
    ctx.fillRect(-4, 12, 8, 28);
    for (let i = 0; i < 10; i++) {
      ctx.save();
      ctx.rotate((Math.PI * 2 * i) / 10 + p.age * 0.2);
      ctx.fillStyle = '#f2c94c';
      ctx.beginPath();
      ctx.ellipse(0, -10, 7, 15, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    ctx.fillStyle = '#593319';
    ctx.beginPath();
    ctx.arc(0, 0, 13, 0, Math.PI * 2);
    ctx.fill();
  } else if (p.def.id === 'wallnut') {
    ctx.fillStyle = '#8d5b29';
    ctx.beginPath();
    ctx.ellipse(0, 2, 18, 24, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#5e3817';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(-10, -9);
    ctx.lineTo(-2, 7);
    ctx.lineTo(8, -3);
    ctx.stroke();
  } else if (p.def.id === 'icepea') {
    ctx.fillStyle = '#81d4f6';
    ctx.beginPath();
    ctx.arc(0, -1, 16, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#6bbee7';
    ctx.fillRect(-4, 10, 8, 26);
    ctx.fillStyle = '#ecfbff';
    ctx.beginPath();
    ctx.arc(-6, -6, 3, 0, Math.PI * 2);
    ctx.arc(6, -6, 3, 0, Math.PI * 2);
    ctx.fill();
  } else if (p.def.id === 'cherry') {
    ctx.strokeStyle = '#3e6a29';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(-2, -18);
    ctx.quadraticCurveTo(0, -32, 9, -28);
    ctx.stroke();
    ctx.fillStyle = '#d93f4b';
    ctx.beginPath();
    ctx.arc(-6, 5, 11, 0, Math.PI * 2);
    ctx.arc(8, 6, 11, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.fillStyle = '#4f8b3f';
    ctx.fillRect(-4, 10, 8, 28);
    ctx.fillStyle = p.def.id === 'repeater' ? '#74d95d' : p.def.c;
    ctx.beginPath();
    ctx.arc(0, -2, 16, 0, Math.PI * 2);
    ctx.fill();
    if (p.def.id === 'repeater') {
      ctx.beginPath();
      ctx.arc(-10, 0, 9, 0, Math.PI * 2);
      ctx.arc(10, 0, 9, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = '#26431f';
    ctx.beginPath();
    ctx.arc(-5, -5, 3, 0, Math.PI * 2);
    ctx.arc(5, -5, 3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.fillStyle = 'rgba(0,0,0,0.28)';
  ctx.fillRect(-20, 34, 40, 5);
  ctx.fillStyle = '#88ef92';
  ctx.fillRect(-20, 34, 40 * (p.hp / p.maxHp), 5);
  if (p.def.id === 'cherry') {
    ctx.fillStyle = 'rgba(255,220,180,0.45)';
    ctx.beginPath();
    ctx.arc(0, 0, 26 + Math.sin(p.fuse * 16) * 2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}
function drawZombie(z) {
  ctx.save();
  ctx.globalAlpha = z.slow > 0 ? 0.88 : 1;
  ctx.translate(z.x, z.y);
  if (z.slow > 0) {
    ctx.shadowColor = '#8edfff';
    ctx.shadowBlur = 10;
  }
  ctx.fillStyle = z.color;
  ctx.beginPath();
  ctx.ellipse(0, 0, z.width * 0.42, z.height * 0.42, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#f2f4dc';
  ctx.beginPath();
  ctx.arc(-10, -10, 5, 0, Math.PI * 2);
  ctx.arc(6, -10, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#203221';
  ctx.beginPath();
  ctx.arc(-9, -9, 2, 0, Math.PI * 2);
  ctx.arc(7, -9, 2, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = '#3b4a22';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(-16, 4);
  ctx.lineTo(-28, 6);
  ctx.moveTo(10, 4);
  ctx.lineTo(24, 8);
  ctx.stroke();
  if (z.type === 'cone' || z.type === 'bucket' || z.type === 'brute') {
    ctx.fillStyle = z.type === 'bucket' ? '#8f98a8' : z.type === 'brute' ? '#4e606d' : '#d49a3d';
    ctx.beginPath();
    ctx.moveTo(0, z.type === 'bucket' ? -40 : -42);
    ctx.lineTo(-18, -10);
    ctx.lineTo(18, -10);
    ctx.fill();
  }
  if (z.type === 'runner') {
    ctx.fillStyle = '#d8efb6';
    ctx.beginPath();
    ctx.moveTo(-22, -6);
    ctx.lineTo(26, -22);
    ctx.lineTo(30, -10);
    ctx.lineTo(-18, 10);
    ctx.fill();
  }
  ctx.fillStyle = 'rgba(0,0,0,0.35)';
  ctx.fillRect(-z.width / 2, -z.height * 0.55, z.width, 5);
  ctx.fillStyle = '#ff8b8b';
  ctx.fillRect(-z.width / 2, -z.height * 0.55, z.width * (z.hp / z.maxHp), 5);
  if (z.armor > 0) {
    ctx.fillStyle = '#d9e8f1';
    ctx.fillRect(-z.width / 2, -z.height * 0.45, z.width * clamp(z.armor / 240, 0, 1), 4);
  }
  ctx.restore();
}
function drawSun(s) {
  ctx.save();
  ctx.translate(s.x, s.y);
  ctx.shadowColor = '#ffe98a';
  ctx.shadowBlur = 14;
  ctx.fillStyle = '#f7df68';
  ctx.beginPath();
  ctx.arc(0, 0, s.r, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.fillStyle = '#fff4b5';
  for (let i = 0; i < 6; i++) {
    ctx.save();
    ctx.rotate((Math.PI * 2 * i) / 6 + S.t);
    ctx.fillRect(-2, -24, 4, 10);
    ctx.restore();
  }
  ctx.fillStyle = '#fff8db';
  ctx.beginPath();
  ctx.arc(0, 0, 6, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}
function drawShot(b) {
  ctx.save();
  ctx.translate(b.x, b.y);
  ctx.shadowColor = b.color;
  ctx.shadowBlur = 8;
  ctx.fillStyle = b.color;
  ctx.beginPath();
  ctx.arc(0, 0, b.r, 0, Math.PI * 2);
  ctx.fill();
  if (b.slow > 0) {
    ctx.strokeStyle = '#dbf7ff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(0, 0, b.r + 4, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}
function drawFx() {
  for (const p of particles) {
    ctx.save();
    ctx.globalAlpha = clamp(p.life / 0.8, 0, 1);
    ctx.fillStyle = p.color;
    ctx.fillRect(p.x, p.y, p.size, p.size);
    ctx.restore();
  }
  for (const f of floaters) {
    ctx.save();
    ctx.globalAlpha = clamp(f.life, 0, 1);
    ctx.fillStyle = f.color;
    ctx.font = 'bold 14px Microsoft YaHei, sans-serif';
    ctx.fillText(f.text, f.x, f.y);
    ctx.restore();
  }
}
function drawOverlay() {
  if (S.paused && !S.over) {
    ctx.fillStyle = 'rgba(8,12,9,0.36)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.font = 'bold 28px Microsoft YaHei, sans-serif';
    ctx.fillText('暂停中', canvas.width / 2, canvas.height / 2 - 10);
    ctx.font = '16px Microsoft YaHei, sans-serif';
    ctx.fillText('按 P 或按钮继续', canvas.width / 2, canvas.height / 2 + 20);
  }
  if (S.over) {
    ctx.fillStyle = 'rgba(7,10,8,0.52)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.font = 'bold 34px Microsoft YaHei, sans-serif';
    ctx.fillText('防线失守', canvas.width / 2, canvas.height / 2 - 18);
    ctx.font = '16px Microsoft YaHei, sans-serif';
    ctx.fillText(`总分 ${S.score} · 第 ${S.wave} 波`, canvas.width / 2, canvas.height / 2 + 14);
  }
}
function draw() {
  drawBoard();
  suns.forEach(drawSun);
  plants.forEach(drawPlant);
  shots.forEach(drawShot);
  zombies.forEach(drawZombie);
  drawFx();
  ctx.save();
  ctx.fillStyle = '#ecf5e9';
  ctx.font = 'bold 18px Microsoft YaHei, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(`阳光 ${Math.floor(S.sun)}`, 24, 26);
  ctx.fillText(`波次 ${S.wave}`, 164, 26);
  ctx.fillText(`基地 ${S.lives}`, 264, 26);
  ctx.fillText(`得分 ${S.score}`, 364, 26);
  ctx.restore();
  drawOverlay();
}

function reflow() {
  statusEl.innerHTML = `
    <div class="mini-grid">
      <div><span class="label">阳光</span><strong>${Math.floor(S.sun)}</strong></div>
      <div><span class="label">得分</span><strong>${S.score}</strong></div>
      <div><span class="label">波次</span><strong>${S.wave}</strong></div>
      <div><span class="label">基地</span><strong>${S.lives}</strong></div>
    </div>
    <div class="meter-row"><div class="meter-meta"><span>太阳风暴</span><span>${Math.floor(S.burst)}%</span></div><div class="meter"><i style="width:${clamp(S.burst,0,100)}%"></i></div></div>
    <div class="meter-row"><div class="meter-meta"><span>战场压力</span><span>${zombies.length + S.queue.length} 个敌人</span></div><div class="meter"><i style="width:${clamp((zombies.length + S.queue.length) * 10,0,100)}%;background:linear-gradient(90deg,#9df07a,#78ffd6)"></i></div></div>
    <div class="meta-line">当前工具：${S.tool === 'shovel' ? '铲子' : `植物 · ${PLANTS.find(p => p.id === S.pick).name}`}</div>
    <div class="meta-line">波次状态：${S.waveText}${S.paused ? ' · 已暂停' : ''}${S.over ? ' · 游戏结束' : ''}</div>
  `;
  logEl.innerHTML = S.logs.map(l => `<p class="${l.tone}">[${l.t}] ${l.msg}</p>`).join('');
  logEl.scrollTop = 0;
  updateCards();
}
function updateCards() {
  const now = S.t;
  cardsEl.querySelectorAll('button').forEach(btn => {
    const def = PLANTS.find(p => p.id === btn.dataset.id);
    const left = Math.max(0, (S.cool.get(def.id) || 0) - now);
    const afford = S.sun >= def.cost;
    btn.classList.toggle('active', S.tool === 'plant' && S.pick === def.id);
    btn.disabled = S.over || !afford || left > 0;
    const foot = btn.querySelector('.card-foot');
    foot.children[0].textContent = left > 0 ? `冷却 ${left.toFixed(1)}s` : '可部署';
    foot.children[1].textContent = afford ? '资源充足' : '阳光不足';
  });
  const ready = S.burst >= 100 && S.burstCd <= 0 && !S.over;
  burstBtn.disabled = !ready;
  burstBtn.classList.toggle('active', ready);
  burstBtn.innerHTML = ready ? '⚡ 太阳风暴 <small>可释放</small>' : `⚡ 太阳风暴 <small>${Math.floor(S.burst)}%</small>`;
}

function updateBackdrop() {
  const hue = 92 + Math.sin(S.t * 0.45) * 8 + Math.min(12, S.wave * 0.8);
  document.documentElement.style.setProperty('--ambient-hue', `${hue.toFixed(2)}deg`);
}

function refreshPause() {
  pauseBtn.textContent = S.paused ? '▶ 继续' : '⏸ 暂停/继续';
}
function reset() {
  plants = [];
  zombies = [];
  shots = [];
  suns = [];
  particles = [];
  floaters = [];
  S.t = 0;
  S.sun = 150;
  S.score = 0;
  S.wave = 0;
  S.lives = 10;
  S.paused = false;
  S.over = false;
  S.tool = 'plant';
  S.pick = 'pea';
  S.burst = 20;
  S.burstCd = 0;
  S.waveText = '准备中';
  S.queue = [];
  S.qTimer = 0;
  S.nextWave = 2.5;
  S.sky = 3.5;
  S.hover = null;
  S.flash = 0;
  S.pulse = 0;
  S.cool = new Map();
  S.logs = [];
  log('布阵完成，准备迎接首波。', 'good');
  refreshPause();
  reflow();
  draw();
}
function togglePause(force) {
  S.paused = typeof force === 'boolean' ? force : !S.paused;
  refreshPause();
  log(S.paused ? '游戏已暂停。' : '游戏继续。', S.paused ? 'warn' : 'good');
  draw();
  reflow();
}

function handleClick(e) {
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) * (canvas.width / rect.width);
  const y = (e.clientY - rect.top) * (canvas.height / rect.height);
  const sun = suns.find(s => !s.collected && Math.hypot(s.x - x, s.y - y) <= s.r + 5);
  if (sun) return collectSun(sun);
  const cell = cellFromPoint(x, y);
  if (!cell) return;
  if (S.tool === 'shovel') removePlant(cell.row, cell.col);
  else placePlant(cell.row, cell.col);
}
function handleMove(e) {
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) * (canvas.width / rect.width);
  const y = (e.clientY - rect.top) * (canvas.height / rect.height);
  S.hover = cellFromPoint(x, y);
}
function handleKeys(e) {
  if (e.key === 'p' || e.key === 'P') return togglePause();
  if (e.key === 'r' || e.key === 'R') return reset();
  if (e.key === 'e' || e.key === 'E') {
    S.tool = S.tool === 'shovel' ? 'plant' : 'shovel';
    eraseBtn.classList.toggle('active', S.tool === 'shovel');
    reflow();
    return;
  }
  if (e.key === ' ') {
    e.preventDefault();
    return triggerBurst();
  }
  const map = { '1': 'pea', '2': 'sunflower', '3': 'wallnut', '4': 'icepea', '5': 'repeater', '6': 'cherry' };
  if (map[e.key]) {
    S.pick = map[e.key];
    S.tool = 'plant';
    eraseBtn.classList.remove('active');
    reflow();
  }
}

function wire() {
  canvas.addEventListener('click', handleClick);
  canvas.addEventListener('pointermove', handleMove);
  canvas.addEventListener('pointerleave', () => { S.hover = null; });
  pauseBtn.addEventListener('click', () => togglePause());
  restartBtn.addEventListener('click', () => reset());
  eraseBtn.addEventListener('click', () => {
    S.tool = S.tool === 'shovel' ? 'plant' : 'shovel';
    eraseBtn.classList.toggle('active', S.tool === 'shovel');
    reflow();
  });
  clearBtn.addEventListener('click', () => clearPlants());
  burstBtn?.addEventListener('click', () => triggerBurst());
  document.addEventListener('keydown', handleKeys);
}

function loop(now) {
  const dt = Math.min(0.033, (now - last) / 1000);
  last = now;
  S.flash = Math.max(0, S.flash - dt);
  tick(dt);
}

buildCards();
wire();
reset();
requestAnimationFrame(loop);
