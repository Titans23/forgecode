const rows = 5;
const cols = 9;

const PLANTS = {
  pea: {
    id: 'pea',
    name: '豌豆射手',
    desc: '每0.9秒发射1枚豌豆',
    cost: 100,
    cooldown: 7000,
    hp: 120,
    shootInterval: 900,
    bulletDamage: 1,
    width: 56,
    height: 56,
  },
  sunflower: {
    id: 'sunflower',
    name: '向日葵',
    desc: '每7秒掉落一颗阳光',
    cost: 50,
    cooldown: 8000,
    hp: 80,
    sunEvery: 7000,
    width: 56,
    height: 56,
  },
  wallnut: {
    id: 'wallnut',
    name: '坚果墙',
    desc: '超厚生命值，短时防守',
    cost: 50,
    cooldown: 15000,
    hp: 600,
    width: 66,
    height: 66,
  },
  potato: {
    id: 'potato',
    name: '土豆雷',
    desc: '被僵尸接触后爆炸',
    cost: 75,
    cooldown: 18000,
    hp: 100,
    explodeDamage: 12,
    width: 58,
    height: 58,
  },
};

const WAVES = [
  { count: 6, spawnInterval: 2.4, hp: 4, speed: 25, damage: 1 },
  { count: 7, spawnInterval: 2.2, hp: 5, speed: 27, damage: 1 },
  { count: 8, spawnInterval: 1.9, hp: 6, speed: 30, damage: 1 },
  { count: 9, spawnInterval: 1.7, hp: 6, speed: 32, damage: 2 },
  { count: 10, spawnInterval: 1.5, hp: 7, speed: 34, damage: 2 },
  { count: 12, spawnInterval: 1.3, hp: 8, speed: 36, damage: 3 },
];

const refs = {
  toolbar: document.getElementById('toolbar'),
  cells: document.getElementById('cells'),
  sunsLayer: document.getElementById('suns'),
  bulletLayer: document.getElementById('projectiles'),
  zombieLayer: document.getElementById('zombies'),
  game: document.getElementById('game'),
  sunCount: document.getElementById('sunCount'),
  waveInfo: document.getElementById('waveInfo'),
  livesInfo: document.getElementById('livesInfo'),
  scoreInfo: document.getElementById('scoreInfo'),
  footerInfo: document.getElementById('footerInfo'),
  overlay: document.getElementById('overlay'),
  overlayText: document.getElementById('overlayText'),
  overlayRestart: document.getElementById('overlayRestart'),
  startBtn: document.getElementById('startBtn'),
  pauseBtn: document.getElementById('pauseBtn'),
  restartBtn: document.getElementById('restartBtn'),
};

const game = {
  sun: 150,
  lives: 5,
  score: 0,
  waveIndex: 0,
  waveSpawned: 0,
  waveDelay: 0,
  running: false,
  paused: false,
  selectedPlant: null,
  lastFrame: 0,
  elapsed: 0,
  spawnTimer: 0,
  randomSunTimer: 0,
  plants: [],
  zombies: [],
  projectiles: [],
  suns: [],
  cellPlants: Array.from({ length: rows }, () => Array(cols).fill(null)),
  plantCooldowns: Object.fromEntries(Object.keys(PLANTS).map((key) => [key, 0])),
  plantTimers: Object.fromEntries(Object.keys(PLANTS).map((key) => [key, 0])),
  plantLastShot: {},
  cellW: 80,
  cellH: 100,
  waveInProgress: false,
  idCounter: 0,
};

const ZOMBIE_W = 56;
const ZOMBIE_H = 70;
const BULLET_W = 14;
const BULLET_H = 10;
const SUN_SIZE = 28;
const FPS_MIN_DELTA = 0.025;

function nextId(prefix) {
  game.idCounter += 1;
  return `${prefix}-${game.idCounter}`;
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function updateMetrics() {
  const rect = refs.cells.getBoundingClientRect();
  game.cellW = rect.width / cols;
  game.cellH = rect.height / rows;
}

function updateHud() {
  refs.sunCount.textContent = Math.floor(game.sun);
  refs.livesInfo.textContent = game.lives;
  refs.scoreInfo.textContent = Math.floor(game.score);
  refs.waveInfo.textContent = `${game.waveIndex + 1} / ${WAVES.length}`;
}

function updateFooter(msg) {
  if (msg) {
    refs.footerInfo.textContent = msg;
  }
}

function buildToolbar() {
  refs.toolbar.innerHTML = '';
  Object.values(PLANTS).forEach((plant) => {
    const btn = document.createElement('button');
    btn.className = 'plant-card';
    btn.dataset.plant = plant.id;

    btn.innerHTML = `
      <div class="name">${plant.name}</div>
      <div class="desc">${plant.desc}</div>
      <div class="cost">${plant.cost} ☼</div>
      <div class="cooldown" data-role="cooldown">可用</div>
    `;

    btn.addEventListener('click', () => selectPlant(plant.id));
    refs.toolbar.appendChild(btn);
  });
  renderToolbarState(0);
}

function buildGrid() {
  refs.cells.innerHTML = '';
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      const cell = document.createElement('button');
      cell.className = 'cell';
      cell.type = 'button';
      cell.dataset.row = r;
      cell.dataset.col = c;
      cell.addEventListener('click', () => placePlant(+r, +c));
      refs.cells.appendChild(cell);
    }
  }
}

function renderToolbarState(now) {
  const cards = document.querySelectorAll('.plant-card');
  cards.forEach((card) => {
    const id = card.dataset.plant;
    const plant = PLANTS[id];
    const nextTime = game.plantCooldowns[id];

    card.classList.toggle('active', game.selectedPlant === id);

    const ready = now >= nextTime;
    if (!ready) {
      card.classList.add('disabled');
    } else {
      card.classList.remove('disabled');
    }

    if (game.sun < plant.cost || !ready) {
      card.classList.add('disabled');
    }

    const cdText = card.querySelector('[data-role="cooldown"]');
    if (!ready) {
      const remain = (nextTime - now) / 1000;
      cdText.textContent = `${Math.max(0, remain).toFixed(1)}s`;
    } else {
      cdText.textContent = '可用';
    }
  });
}

function selectPlant(id) {
  const now = performance.now();
  const plant = PLANTS[id];
  if (!plant) return;
  if (game.sun < plant.cost || now < game.plantCooldowns[id]) {
    game.selectedPlant = null;
    renderToolbarState(now);
    return;
  }

  game.selectedPlant = id;
  renderToolbarState(now);
  updateFooter(`已选择：${plant.name}（点击任意格子放置）`);
}

function cellElement(row, col) {
  const idx = row * cols + col;
  return refs.cells.children[idx];
}

function addPlant(row, col) {
  const plantDef = PLANTS[game.selectedPlant];
  const now = performance.now();

  if (!plantDef) return null;
  if (game.selectedPlant == null) return null;
  if (game.sun < plantDef.cost) return null;
  if (now < game.plantCooldowns[game.selectedPlant]) return null;
  if (game.cellPlants[row][col]) return null;

  const x = col * game.cellW + (game.cellW - plantDef.width) / 2;
  const y = row * game.cellH + 10;

  const el = document.createElement('div');
  el.className = `plant ${plantDef.id}`;
  el.dataset.row = row;
  el.dataset.col = col;
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  el.style.setProperty('--hp-ratio', 1);

  const hpWrap = document.createElement('div');
  hpWrap.className = 'hp-wrap';
  const hpFill = document.createElement('div');
  hpFill.className = 'hp-fill';
  hpWrap.appendChild(hpFill);

  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = plantDef.name;

  el.appendChild(hpWrap);
  el.appendChild(title);

  refs.cells.appendChild(el);

  const plantObj = {
    id: nextId('p'),
    type: plantDef.id,
    def: plantDef,
    row,
    col,
    x,
    y,
    width: plantDef.width,
    height: plantDef.height,
    hp: plantDef.hp,
    maxHp: plantDef.hp,
    el,
    lastShot: 0,
    lastSun: now,
    explodeReady: false,
    removed: false,
  };

  game.plants.push(plantObj);
  game.cellPlants[row][col] = plantObj;
  game.sun -= plantDef.cost;
  game.plantCooldowns[plantDef.id] = now + plantDef.cooldown;

  game.selectedPlant = null;
  renderToolbarState(now);
  updateHud();
  return plantObj;
}

function placePlant(row, col) {
  if (addPlant(row, col)) {
    const cell = cellElement(row, col);
    cell.disabled = true;
    updateFooter('放置成功。可继续选择其他植物继续布局。');
  }
}

function spawnFloatingSun(x = null, y = null, value = 25) {
  const sunX = x == null ? Math.random() * (cols * game.cellW - SUN_SIZE) : x;
  const sunY = y == null ? Math.random() * (rows * game.cellH - SUN_SIZE) : y;

  const el = document.createElement('div');
  el.className = 'sun';
  el.style.left = `${sunX}px`;
  el.style.top = `${sunY}px`;

  const id = nextId('s');
  const sunObj = {
    id,
    x: sunX,
    y: sunY,
    value,
    life: 0,
    maxLife: 10,
    el,
  };

  const handler = () => {
    collectSun(id);
  };
  el.addEventListener('click', handler);
  sunObj.handler = handler;
  refs.sunsLayer.appendChild(el);
  game.suns.push(sunObj);

  return sunObj;
}

function collectSun(id) {
  const index = game.suns.findIndex((s) => s.id === id);
  if (index < 0) return;
  const s = game.suns[index];
  game.sun += s.value;
  s.el.remove();
  game.suns.splice(index, 1);
  updateHud();
  const now = performance.now();
  renderToolbarState(now);
}

function removePlantByObj(obj) {
  if (!obj || obj.removed) return;
  obj.removed = true;
  const { row, col } = obj;
  if (game.cellPlants[row][col] === obj) {
    game.cellPlants[row][col] = null;
    const cell = cellElement(row, col);
    if (cell) cell.disabled = false;
  }

  if (obj.el) obj.el.remove();
  const i = game.plants.indexOf(obj);
  if (i >= 0) game.plants.splice(i, 1);
}

function removeZombieByObj(obj) {
  if (!obj) return;
  if (obj.el) obj.el.remove();
  const i = game.zombies.indexOf(obj);
  if (i >= 0) game.zombies.splice(i, 1);
}

function updatePlantHp(plant) {
  const ratio = clamp(plant.hp / plant.maxHp, 0, 1);
  plant.el.style.setProperty('--hp-ratio', ratio.toFixed(3));
  const hp = plant.el.querySelector('.hp-fill');
  if (hp) hp.style.width = `${ratio * 100}%`;

  if (plant.hp <= 0) {
    if (plant.type === 'potato' && !plant.explodeReady) {
      plant.explodeReady = true;
      explodePotato(plant);
    }
    removePlantByObj(plant);
  }
}

function explodePotato(plant) {
  const blastX = plant.x + plant.width / 2;
  const blastY = plant.y + plant.height / 2;
  const blastRadius = game.cellW * 0.7;

  for (const z of [...game.zombies]) {
    const dz = z.x + ZOMBIE_W / 2 - blastX;
    const dy = z.y + ZOMBIE_H / 2 - blastY;
    const dist2 = dz * dz + dy * dy;
    if (dist2 <= blastRadius * blastRadius) {
      z.hp -= PLANTS.potato.explodeDamage;
      if (z.hp <= 0) {
        game.score += 50;
        removeZombieByObj(z);
      }
    }
  }

  // 爆炸视觉
  const boom = document.createElement('div');
  boom.textContent = '💥';
  boom.style.position = 'absolute';
  boom.style.left = `${blastX - 14}px`;
  boom.style.top = `${blastY - 18}px`;
  boom.style.fontSize = '26px';
  boom.style.pointerEvents = 'none';
  refs.cells.appendChild(boom);
  setTimeout(() => boom.remove(), 450);
}

function spawnZombie() {
  const wave = WAVES[game.waveIndex];
  const row = Math.floor(Math.random() * rows);
  const zObj = {
    id: nextId('z'),
    row,
    x: cols * game.cellW + 24,
    y: row * game.cellH + 14,
    width: ZOMBIE_W,
    height: ZOMBIE_H,
    maxHp: wave.hp,
    hp: wave.hp,
    speed: wave.speed,
    damage: wave.damage,
    attackTimer: 0,
    target: null,
    el: document.createElement('div'),
    state: 'walk',
    walkDir: -1,
  };

  zObj.el.className = 'zombie walk';
  zObj.el.style.left = `${zObj.x}px`;
  zObj.el.style.top = `${zObj.y}px`;
  refs.zombieLayer.appendChild(zObj.el);

  game.zombies.push(zObj);
  game.waveSpawned += 1;
}

function shootBullet(plant, now) {
  if (plant.type !== 'pea') return;
  if (now - plant.lastShot < plant.def.shootInterval) return;
  const rowBullets = game.projectiles;

  const el = document.createElement('div');
  el.className = 'projectile';
  el.style.left = `${plant.x + plant.width + 8}px`;
  el.style.top = `${plant.y + 24}px`;

  const bullet = {
    id: nextId('b'),
    row: plant.row,
    x: plant.x + plant.width + 8,
    y: plant.y + 24,
    speed: 430,
    damage: plant.def.bulletDamage,
    el,
  };

  refs.bulletLayer.appendChild(el);
  plant.lastShot = now;
  rowBullets.push(bullet);
}

function updateBullet(dt) {
  for (const bullet of [...game.projectiles]) {
    bullet.x += bullet.speed * dt;
    bullet.el.style.left = `${bullet.x}px`;

    let hit = false;
    for (const zombie of [...game.zombies]) {
      if (zombie.row !== bullet.row) continue;
      if (bullet.x > zombie.x + ZOMBIE_W || bullet.x + BULLET_W < zombie.x) continue;
      zombie.hp -= bullet.damage;
      hit = true;
      if (zombie.hp <= 0) {
        game.score += 20;
        removeZombieByObj(zombie);
        updateHud();
      }
      break;
    }

    if (hit || bullet.x > cols * game.cellW + 30) {
      bullet.el.remove();
      const idx = game.projectiles.indexOf(bullet);
      if (idx >= 0) game.projectiles.splice(idx, 1);
    }
  }
}

function getPlantAtRow(row) {
  return game.cellPlants[row].filter(Boolean);
}

function getRightmostPlantIntersectingZombie(z) {
  const plantsInRow = getPlantAtRow(z.row);
  if (!plantsInRow.length) return null;
  for (let i = plantsInRow.length - 1; i >= 0; i -= 1) {
    const p = plantsInRow[i];
    if (!p || p.removed) continue;
    if (p.el && z.x < p.x + p.width && z.x + ZOMBIE_W > p.x) {
      return p;
    }
  }
  return null;
}

function removeZombieIfDead(z) {
  if (z.hp > 0) return;
  if (z.el) {
    z.el.classList.remove('eat', 'walk');
  }
  game.score += 40;
  removeZombieByObj(z);
  updateHud();
}

function updateZombies(dt) {
  for (const z of [...game.zombies]) {
    const target = getRightmostPlantIntersectingZombie(z);

    if (target) {
      z.state = 'eat';
      z.el.classList.remove('walk');
      z.el.classList.add('eat');
      z.target = target;
      z.attackTimer += dt;
      if (z.attackTimer >= 1) {
        z.attackTimer = 0;
        target.hp -= z.damage;
        updatePlantHp(target);
      }
      continue;
    }

    if (z.state === 'eat') {
      z.state = 'walk';
      z.el.classList.remove('eat');
      z.el.classList.add('walk');
      z.target = null;
    }

    z.x -= z.speed * dt;
    z.el.style.left = `${z.x}px`;

    if (z.x < -ZOMBIE_W) {
      game.lives -= 1;
      updateHud();
      removeZombieByObj(z);
      updateFooter('僵尸突破防线，你损失1生命！');

      if (game.lives <= 0) {
        gameOver(false);
      }
    }
  }
}

function updatePlants(now, dt) {
  for (const plant of [...game.plants]) {
    if (plant.removed) continue;

    if (plant.type === 'pea') {
      shootBullet(plant, now);
    }

    if (plant.type === 'sunflower' && now - plant.lastSun >= plant.def.sunEvery) {
      plant.lastSun = now;
      spawnFloatingSun(plant.x + plant.width / 2, plant.y + plant.height / 2, 25);
    }
  }

  updateHpBars();
}

function updateHpBars() {
  for (const p of game.plants) {
    const ratio = clamp(p.hp / p.maxHp, 0, 1);
    p.el.style.setProperty('--hp-ratio', ratio.toFixed(3));
    const fill = p.el.querySelector('.hp-fill');
    if (fill) fill.style.width = `${ratio * 100}%`;
  }
}

function updateSuns(dt) {
  for (const sun of [...game.suns]) {
    sun.life += dt;
    if (sun.life >= sun.maxLife) {
      if (sun.el) sun.el.remove();
      const idx = game.suns.indexOf(sun);
      if (idx >= 0) game.suns.splice(idx, 1);
    }
  }
}

function checkWaveProgress(dt) {
  const wave = WAVES[game.waveIndex];

  if (game.waveDelay > 0) {
    game.waveDelay -= dt;
    if (game.waveDelay <= 0) {
      game.waveDelay = 0;
      game.waveSpawned = 0;
      game.spawnTimer = 0;
      updateFooter(`第 ${game.waveIndex + 1} 波开始！`);
    }
    return;
  }

  game.spawnTimer += dt;

  if (game.waveSpawned < wave.count && game.spawnTimer >= wave.spawnInterval) {
    game.spawnTimer = 0;
    spawnZombie();
  }

  if (game.waveSpawned >= wave.count && game.zombies.length === 0) {
    if (game.waveIndex < WAVES.length - 1) {
      game.waveIndex += 1;
      game.waveDelay = 4;
      updateFooter(`当前波已清空，下一波将在 ${Math.ceil(game.waveDelay)} 秒后开始。`);
    } else {
      gameOver(true);
    }
  }
}

function gameOver(win) {
  game.running = false;
  game.paused = false;

  if (win) {
    refs.overlayText.textContent = `恭喜通关！最终分数：${Math.floor(game.score)}，阳光：${Math.floor(game.sun)}`;
  } else {
    refs.overlayText.textContent = `游戏结束！僵尸吞掉了你的大脑。最终分数：${Math.floor(game.score)}，阳光：${Math.floor(game.sun)}`;
  }

  refs.overlay.classList.add('show');
  updateFooter(win ? '胜利！' : '失败！');
}

function spawnFromSky(dt) {
  game.randomSunTimer += dt;
  if (game.randomSunTimer >= 5 + Math.random() * 4) {
    game.randomSunTimer = 0;
    const row = Math.floor(Math.random() * rows);
    const x = Math.random() * (cols * game.cellW - SUN_SIZE);
    const y = row * game.cellH + Math.random() * (game.cellH - SUN_SIZE);
    spawnFloatingSun(x, y, 25);
  }
}

function resetEntities() {
  for (const p of [...game.plants]) {
    p.el.remove();
  }
  for (const z of [...game.zombies]) {
    z.el.remove();
  }
  for (const b of [...game.projectiles]) {
    b.el.remove();
  }
  for (const s of [...game.suns]) {
    s.el.remove();
  }

  for (const c of [...refs.cells.children]) {
    c.disabled = false;
  }

  game.plants = [];
  game.zombies = [];
  game.projectiles = [];
  game.suns = [];
  game.waveSpawned = 0;
  game.spawnTimer = 0;
  game.randomSunTimer = 0;
  game.waveDelay = 0;
  game.elapsed = 0;
  game.selectedPlant = null;
  game.cellPlants = Array.from({ length: rows }, () => Array(cols).fill(null));
  game.plantCooldowns = Object.fromEntries(Object.keys(PLANTS).map((key) => [key, 0]));
}

function restartGame() {
  refs.overlay.classList.remove('show');
  game.sun = 150;
  game.lives = 5;
  game.score = 0;
  game.waveIndex = 0;
  game.running = false;
  game.paused = false;
  resetEntities();
  updateHud();
  renderToolbarState(performance.now());
  updateFooter('游戏已重置。点击“开始”后发起第一波僵尸。');
}

function update(dt) {
  const now = performance.now();
  updateFooter(`第 ${game.waveIndex + 1} 波 · 下一波准备: ${game.waveDelay > 0 ? `${Math.ceil(game.waveDelay)}秒` : '进行中'}`);
  game.elapsed += dt;

  renderToolbarState(now);
  updatePlants(now, dt);
  updateSuns(dt);
  updateBullet(dt);
  updateZombies(dt);
  checkWaveProgress(dt);
  spawnFromSky(dt);
  updateHud();
}

function gameTick(timestamp) {
  if (!game.lastFrame) game.lastFrame = timestamp;
  const dt = Math.min((timestamp - game.lastFrame) / 1000, FPS_MIN_DELTA);
  game.lastFrame = timestamp;

  if (game.running && !game.paused) {
    update(dt);
  }

  requestAnimationFrame(gameTick);
}

function bindEvents() {
  refs.startBtn.addEventListener('click', () => {
    if (!game.running) {
      game.running = true;
      game.paused = false;
      updateFooter('开始！僵尸来袭。');
      if (game.plants.length === 0) {
        updateFooter('提示：你还没种任何植物，尽快布阵后再应对僵尸。');
      }
    }
  });

  refs.pauseBtn.addEventListener('click', () => {
    if (!game.running) return;
    game.paused = !game.paused;
    refs.pauseBtn.textContent = game.paused ? '继续' : '暂停';
    if (!game.paused) updateFooter('继续防守中。');
    else updateFooter('已暂停。');
  });

  refs.restartBtn.addEventListener('click', restartGame);
  refs.overlayRestart.addEventListener('click', restartGame);

  window.addEventListener('resize', () => {
    const oldW = game.cellW;
    updateMetrics();
    // 仅在网格尺寸变化明显时重排实体位置，避免复杂坐标抖动
    if (Math.abs(oldW - game.cellW) > 1) {
      for (const p of game.plants) {
        const x = p.col * game.cellW + (game.cellW - p.width) / 2;
        const y = p.row * game.cellH + 10;
        p.x = x;
        p.y = y;
        p.el.style.left = `${x}px`;
        p.el.style.top = `${y}px`;
      }
      for (const z of game.zombies) {
        z.el.style.top = `${z.row * game.cellH + 14}px`;
      }
    }
  });
}

function initGame() {
  updateMetrics();
  buildToolbar();
  buildGrid();
  bindEvents();
  restartGame();
  requestAnimationFrame(gameTick);
}

function updateCleanup() {
  for (const p of game.plants) {
    if (p.hp <= 0) {
      removePlantByObj(p);
    }
  }
}

function tickExtraCleanup() {
  // cleanup dead zombies/beans maybe if any were removed externally
  updateCleanup();
  updateHud();
  renderToolbarState(performance.now());
}

initGame();
setInterval(tickExtraCleanup, 500);
