const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
const statusEl = document.getElementById('status');
const cardsEl = document.getElementById('cards');
const logEl = document.getElementById('log');
const pauseBtn = document.getElementById('pauseBtn');
const restartBtn = document.getElementById('restartBtn');
const eraseBtn = document.getElementById('eraseBtn');
const clearBtn = document.getElementById('clearBtn');

const CELL_W = 88;
const CELL_H = 96;
const ROWS = 5;
const COLS = 9;
const BOARD_W = CELL_W * COLS;
const BOARD_H = CELL_H * ROWS;
const GAME_W = BOARD_W + 220;
const GAME_H = BOARD_H + 8;
canvas.width = GAME_W;
canvas.height = BOARD_H + 2; // keep integer pixel for canvas drawing

const ZOMBIE_TYPES = {
  normal:   { name:'普通僵尸', hp: 120, speed: 23, attack: 10, attackInterval: 900, reward: 8, color: '#4da165' },
  conehead: { name:'路障僵尸', hp: 190, speed: 19, attack: 12, attackInterval: 900, reward: 10, color: '#5f8a62' },
  bucket:   { name:'铁桶僵尸', hp: 260, speed: 15, attack: 14, attackInterval: 850, reward: 12, color: '#8a5a4a' },
  screendoor:{ name:'铁门僵尸', hp: 300, speed: 28, attack: 13, attackInterval: 800, reward: 15, color: '#5f5f97' },
};

const PLANT_TYPES = {
  sunflower: {
    name: '向日葵', cost: 50, cooldown: 7000, emoji: '☀️', hp: 70,
    update(pl, g, dt) {
      pl.sunTimer += dt;
      if (pl.sunTimer >= 8000) {
        g.spawnSun(pl.x + CELL_W * 0.55, pl.y + CELL_H * 0.6);
        pl.sunTimer = 0;
      }
    },
  },
  peashooter: {
    name: '豌豆射手', cost: 100, cooldown: 7000, emoji: '🌱', hp: 120,
    shootInterval: 850,
    update(pl, g, dt) {
      pl.fireTimer += dt;
      const hasTarget = g.hasZombieAhead(pl.row, pl.x);
      if (hasTarget && pl.fireTimer >= PLANT_TYPES.peashooter.shootInterval) {
        g.spawnProjectile({
          x: pl.x + CELL_W,
          y: pl.y + CELL_H * 0.55,
          vx: 310,
          radius: 6,
          damage: 20,
          slow: 0,
          row: pl.row,
        });
        pl.fireTimer = 0;
      }
    },
  },
  snowpea: {
    name: '寒冰射手', cost: 175, cooldown: 9000, emoji: '❄️', hp: 120,
    shootInterval: 700,
    update(pl, g, dt) {
      pl.fireTimer += dt;
      const hasTarget = g.hasZombieAhead(pl.row, pl.x);
      if (hasTarget && pl.fireTimer >= PLANT_TYPES.snowpea.shootInterval) {
        g.spawnProjectile({
          x: pl.x + CELL_W,
          y: pl.y + CELL_H * 0.55,
          vx: 340,
          radius: 5,
          damage: 18,
          slow: 0.55,
          row: pl.row,
        });
        pl.fireTimer = 0;
      }
    },
  },
  repeater: {
    name: '双发射手', cost: 180, cooldown: 10000, emoji: '🔫', hp: 120,
    shootInterval: 1400,
    update(pl, g, dt) {
      pl.fireTimer += dt;
      if (pl.fireTimer >= PLANT_TYPES.repeater.shootInterval && g.hasZombieAhead(pl.row, pl.x)) {
        for (let i = 0; i < 2; i++) {
          g.spawnProjectile({
            x: pl.x + CELL_W + i * 5,
            y: pl.y + CELL_H * (0.52 + i * 0.05),
            vx: 340,
            radius: 6,
            damage: 12,
            slow: 0,
            row: pl.row,
          });
        }
        pl.fireTimer = 0;
      }
    },
  },
  wallnut: {
    name: '坚果', cost: 50, cooldown: 5000, emoji: '🥥', hp: 420,
    update() {},
  },
  cherry: {
    name: '樱桃炸弹', cost: 150, cooldown: 18000, emoji: '🍒', hp: 80,
    fuse: 1600,
    update(pl, g, dt) {
      pl.fuseTimer += dt;
      if (!pl.exploded && pl.fuseTimer >= PLANT_TYPES.cherry.fuse) {
        pl.exploded = true;
        const cx = pl.x + CELL_W * 0.5;
        const cy = pl.y + CELL_H * 0.5;
        g.zombies.forEach((z) => {
          const dx = z.x - cx;
          const dy = z.y - cy;
          const dist = Math.hypot(dx, dy);
          if (dist < 165) {
            z.takeDamage(280);
          }
        });
        g.log(`${pl.label} 引爆了！`);
        g.killPlantsInRadius(pl.row, pl.x, 1.1);
        pl.dieSoon = true;
      }
    },
  },
};

const WAVE_SCRIPTS = [
  { name: '第一波', total: 12, pool: ['normal', 'normal', 'normal', 'normal', 'conehead'], interval: 1100 },
  { name: '第二波', total: 18, pool: ['normal', 'normal', 'normal', 'conehead', 'bucket'], interval: 900 },
  { name: '第三波', total: 24, pool: ['normal', 'conehead', 'conehead', 'bucket', 'screendoor'], interval: 700 },
  { name: '狂风卷土', total: 34, pool: ['normal', 'conehead', 'bucket', 'bucket', 'screendoor', 'screendoor'], interval: 580 },
];

class Plant {
  constructor(type, col, row) {
    this.type = type;
    const def = PLANT_TYPES[type];
    this.label = def.name;
    this.row = row;
    this.col = col;
    this.x = col * CELL_W;
    this.y = row * CELL_H;
    this.w = CELL_W;
    this.h = CELL_H;
    this.hp = def.hp;
    this.maxHp = def.hp;
    this.fireTimer = 0;
    this.sunTimer = 0;
    this.fuseTimer = 0;
    this.exploded = false;
    this.dieSoon = false;
  }
  update(game, dt) {
    const def = PLANT_TYPES[this.type];
    def.update(this, game, dt);
  }
  takeDamage(d) {
    this.hp -= d;
    return this.hp <= 0;
  }
  render(gfx) {
    const { x, y, type } = this;
    const def = PLANT_TYPES[type];
    const ratio = this.hp / this.maxHp;

    gfx.fillStyle = 'rgba(22, 84, 50, 0.8)';
    gfx.fillRect(x + 4, y + 6, CELL_W - 8, CELL_H - 12);
    gfx.fillStyle = 'rgba(255,255,255,0.2)';
    gfx.fillRect(x + 8, y + 10, (CELL_W - 16) * ratio, 8);

    gfx.fillStyle = '#f8ffd9';
    gfx.font = '30px sans-serif';
    gfx.textAlign = 'center';
    gfx.fillText(def.emoji, x + CELL_W / 2, y + CELL_H / 2 + 4);

    gfx.fillStyle = '#d6ffbc';
    gfx.font = '12px monospace';
    gfx.fillText(`${Math.max(0, this.hp).toFixed(0)}/${this.maxHp}`, x + CELL_W / 2, y + CELL_H - 10);
  }
}

class Zombie {
  constructor(type, row) {
    const def = ZOMBIE_TYPES[type];
    this.type = type;
    this.def = def;
    this.row = row;
    this.x = BOARD_W + 12;
    this.y = row * CELL_H + 8;
    this.w = 52;
    this.h = 64;
    this.hp = def.hp;
    this.maxHp = def.hp;
    this.speed = def.speed;
    this.attackTimer = 0;
    this.dead = false;
    this.slowUntil = 0;
  }
  update(game, dt) {
    const now = game.time;
    let target = null;
    let nearestDist = Infinity;

    for (const p of game.plants) {
      if (p.row !== this.row) continue;
      if (p.x <= this.x && this.x - p.x < nearestDist) {
        const overlap = this.x - p.x;
        if (overlap <= p.w + 4) {
          nearestDist = overlap;
          target = p;
        }
      }
    }

    if (target) {
      this.attackTimer += dt;
      if (this.attackTimer >= this.def.attackInterval) {
        if (target.takeDamage(this.def.attack)) {
          game.log(`一株${target.label}被咬碎了`);
        }
        this.attackTimer = 0;
      }
    } else {
      this.attackTimer = 0;
      const speed = now < this.slowUntil ? this.speed * 0.45 : this.speed;
      this.x -= speed * dt / 1000;
      if (this.x + this.w < 0) {
        game.lives--;
        game.log('一只僵尸闯入房屋，生命值-1');
        this.dead = true;
      }
    }
  }
  takeDamage(dmg) {
    this.hp -= dmg;
    return this.hp <= 0;
  }
  slow(factor, ms, now) {
    if (factor > 0) {
      this.slowUntil = Math.max(this.slowUntil, now + ms);
    }
  }
  render(gfx) {
    const ratio = this.hp / this.maxHp;
    gfx.save();
    gfx.fillStyle = this.def.color;
    gfx.fillRect(this.x, this.y + 16, this.w, this.h - 16);
    gfx.fillStyle = '#ffd8a8';
    gfx.fillRect(this.x + 12, this.y - 6, this.w - 24, 10);
    gfx.fillStyle = '#ff9f9f';
    gfx.fillRect(this.x + 12, this.y - 6, Math.max(0, (this.w - 24) * ratio), 10);
    gfx.fillStyle = '#0e2e1a';
    gfx.font = 'bold 12px monospace';
    gfx.fillText(ZOMBIE_TYPES[this.type].name, this.x + 2, this.y + 10);
    gfx.restore();
  }
}

class Projectile {
  constructor(data) {
    this.x = data.x;
    this.y = data.y;
    this.vx = data.vx;
    this.radius = data.radius;
    this.damage = data.damage;
    this.slow = data.slow || 0;
    this.row = data.row;
  }
  update(game, dt) {
    this.x += this.vx * dt / 1000;
    if (this.x > BOARD_W + 30) return true;

    for (const z of game.zombies) {
      if (z.row !== this.row) continue;
      const dz = z.x - this.x;
      const dy = z.y + 40 - this.y;
      if (dz < 0 && dz > -8 && Math.abs(dy) < 24) {
        if (z.takeDamage(this.damage)) {
          game.kills++;
          game.sun += 15;
          game.log(`击杀一只僵尸(+15阳光)`);
        }
        if (this.slow > 0) z.slow(this.slow, 1200, game.time);
        return true;
      }
    }
    return false;
  }
  render(gfx) {
    gfx.fillStyle = '#ffe97f';
    gfx.beginPath();
    gfx.arc(this.x, this.y, this.radius, 0, Math.PI * 2);
    gfx.fill();
    if (this.slow > 0) {
      gfx.fillStyle = '#9ee7ff';
      gfx.beginPath();
      gfx.arc(this.x + 10, this.y - 2, this.radius - 1, 0, Math.PI * 2);
      gfx.fill();
    }
  }
}

class Sun {
  constructor(x, y, speed = 26) {
    this.x = x;
    this.y = y;
    this.r = 16;
    this.vy = speed;
    this.collected = false;
    this.value = 25;
  }
  update(dt) {
    this.y += this.vy * dt / 1000;
  }
  contains(mx, my) {
    return (mx - this.x) ** 2 + (my - this.y) ** 2 <= this.r * this.r;
  }
  render(gfx) {
    gfx.fillStyle = '#ffec7e';
    gfx.beginPath();
    gfx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
    gfx.fill();
    gfx.fillStyle = '#ffcc2c';
    gfx.beginPath();
    gfx.arc(this.x, this.y, this.r * 0.6, 0, Math.PI * 2);
    gfx.fill();
  }
}

class Game {
  constructor() {
    this.reset();
    this.renderCards();
    this.lastTs = 0;
    requestAnimationFrame(this.loop.bind(this));
  }

  reset() {
    this.plants = [];
    this.zombies = [];
    this.projectiles = [];
    this.suns = [];
    this.sun = 180;
    this.lives = 6;
    this.time = 0;
    this.kills = 0;
    this.logLines = ['游戏启动：按卡牌开始防守。'];
    this.selected = null;
    this.eraseMode = false;
    this.paused = false;
    this.waveIndex = 0;
    this.waveSpawned = 0;
    this.totalSpawnedThisWave = 0;
    this.spawnTimer = 0;
    this.currentWaveStarted = false;
    this.waveCooldown = 0;
    this.lastUsed = {};
    this.state = 'playing';
    this.skyTimer = 0;
    this.clearTimer = 0;
  }

  renderCards() {
    cardsEl.innerHTML = '';
    Object.entries(PLANT_TYPES).forEach(([k, v]) => {
      const b = document.createElement('button');
      b.className = 'card';
      b.dataset.type = k;
      b.innerText = `${v.emoji} ${v.name}\n花费:${v.cost} 阳光`;
      b.addEventListener('click', () => {
        this.selectPlant(k);
      });
      b.title = `冷却: ${(v.cooldown/1000).toFixed(1)}s`;
      cardsEl.appendChild(b);
    });
    this.cardButtons = Array.from(cardsEl.querySelectorAll('button'));
    this.updateCardStates();
  }

  log(msg) {
    this.logLines.push(msg);
    while (this.logLines.length > 9) this.logLines.shift();
    logEl.textContent = this.logLines.join('\n');
  }

  updateCardStates() {
    const now = this.time;
    for (const b of this.cardButtons) {
      const t = b.dataset.type;
      const def = PLANT_TYPES[t];
      const cd = this.lastUsed[t] ? Math.max(0, def.cooldown - (now - this.lastUsed[t])) : 0;
      const available = this.sun >= def.cost && this.state === 'playing' && cd <= 0;
      b.disabled = !available;
      if (cd > 0) {
        b.innerText = `${def.emoji} ${def.name}\n冷却:${(cd / 1000).toFixed(1)}s`;
      } else {
        b.innerText = `${def.emoji} ${def.name}\n花费:${def.cost} 阳光`;
      }
      if (this.selected === t) b.classList.add('active'); else b.classList.remove('active');
    }
    eraseBtn.classList.toggle('active', this.eraseMode);
  }

  selectPlant(type) {
    this.eraseMode = false;
    this.selected = this.selected === type ? null : type;
    this.updateCardStates();
  }

  spawnSun(x, y, speed = 18) {
    this.suns.push(new Sun(x, y, speed));
  }

  hasZombieAhead(row, x) {
    return this.zombies.some((z) => z.row === row && z.x > x);
  }

  spawnProjectile(payload) {
    this.projectiles.push(new Projectile(payload));
  }

  spawnZombie(type, row) {
    const z = new Zombie(type, row);
    this.zombies.push(z);
    this.waveSpawned++;
    this.totalSpawnedThisWave++;
  }

  killPlantsInRadius(row, x, cells) {
    const left = x - cells * CELL_W;
    const right = x + cells * CELL_W;
    for (const p of this.plants) {
      if (p.row >= row - 1 && p.row <= row + 1 && p.x >= left && p.x <= right) {
        p.hp = 0;
      }
    }
  }

  runWave(dt) {
    if (!this.currentWaveStarted) {
      this.currentWaveStarted = true;
      this.log(`=== ${WAVE_SCRIPTS[this.waveIndex].name} 开始 ===`);
    }

    const wave = WAVE_SCRIPTS[this.waveIndex];
    if (!this.wavePlan || this.wavePlan.length === 0) {
      this.wavePlan = [];
      for (let i = 0; i < wave.total; i++) {
        this.wavePlan.push(wave.pool[Math.floor(Math.random() * wave.pool.length)]);
      }
      this.shuffle(this.wavePlan);
      this.totalSpawnedThisWave = 0;
      this.spawnTimer = 0;
    }

    if (this.wavePlan.length > 0) {
      this.spawnTimer += dt;
      if (this.spawnTimer >= wave.interval) {
        const t = this.wavePlan.shift();
        const row = Math.floor(Math.random() * ROWS);
        this.spawnZombie(t, row);
        this.spawnTimer = 0;
      }
    } else {
      if (this.zombies.length === 0) {
        this.waveCooldown += dt;
        if (this.waveCooldown >= 3200) {
          this.waveCooldown = 0;
          if (this.waveIndex < WAVE_SCRIPTS.length - 1) {
            this.waveIndex++;
            this.wavePlan = null;
            this.currentWaveStarted = false;
            this.sun += 50;
            this.log('波次完成：奖励阳光 +50');
          } else {
            this.state = 'win';
            this.log('你成功击退所有僵尸，胜利！');
          }
        }
      }
    }

    this.updateStatusText();
  }

  updateStatusText() {
    const waveName = WAVE_SCRIPTS[this.waveIndex]?.name || '结束';
    const remain = this.wavePlan ? this.wavePlan.length : 0;
    const status = [
      `阳光: ${this.sun}`,
      `生命: ${this.lives}`,
      `波次: ${waveName}`,
      `本波剩余僵尸: ${remain}`,
      `总击杀: ${this.kills}`,
      `状态: ${this.state === 'playing' ? '进行中' : this.state === 'win' ? '胜利' : '失败'}`,
      `已选: ${this.eraseMode ? '铲子' : (this.selected ? PLANT_TYPES[this.selected].name : '无')}`,
    ];
    statusEl.innerHTML = `<p>${status.join('</p><p>')}</p>`;
  }

  clearAll() {
    this.plants = [];
    this.log('清空草坪');
  }

  placePlant(col, row) {
    if (this.state !== 'playing') return;
    if (col < 0 || col >= COLS || row < 0 || row >= ROWS) return;
    if (this.eraseMode) {
      const idx = this.plants.findIndex((p) => p.col === col && p.row === row);
      if (idx >= 0) {
        this.plants.splice(idx, 1);
        this.log('移除植物');
      }
      return;
    }

    if (!this.selected) {
      this.log('请先选择植物卡牌');
      return;
    }

    const def = PLANT_TYPES[this.selected];
    const now = this.time;
    const last = this.lastUsed[this.selected] || -1e9;
    if (now - last < def.cooldown) {
      this.log('该植物还在冷却');
      return;
    }

    if (this.sun < def.cost) {
      this.log('阳光不足');
      return;
    }

    if (this.plants.some((p) => p.col === col && p.row === row)) {
      this.log('该格子已被占用');
      return;
    }

    this.sun -= def.cost;
    this.plants.push(new Plant(this.selected, col, row));
    this.lastUsed[this.selected] = now;
    this.log(`种植了 ${def.name}`);
    this.selected = null;
    this.updateCardStates();
  }

  handleClick(e) {
    const rect = canvas.getBoundingClientRect();
    const sx = (canvas.width / rect.width);
    const sy = (canvas.height / rect.height);
    const x = (e.clientX - rect.left) * sx;
    const y = (e.clientY - rect.top) * sy;

    for (let i = this.suns.length - 1; i >= 0; i--) {
      const s = this.suns[i];
      if (s.contains(x, y)) {
        this.sun += s.value;
        this.log('收集阳光 +25');
        this.suns.splice(i, 1);
        return;
      }
    }

    if (x >= 0 && x < BOARD_W && y >= 0 && y < BOARD_H) {
      const col = Math.floor(x / CELL_W);
      const row = Math.floor(y / CELL_H);
      this.placePlant(col, row);
    }
  }

  update(dt) {
    if (this.state !== 'playing' || this.paused) return;

    this.time += dt;

    this.skyTimer += dt;
    if (this.skyTimer >= 4500) {
      this.skyTimer = 0;
      this.spawnSun(Math.random() * (BOARD_W - 20) + 10, -20, 35);
    }

    if (this.wavePlan === null) this.wavePlan = null;
    if (this.wavePlan === undefined || this.wavePlan === null) {
      const wave = WAVE_SCRIPTS[this.waveIndex];
      this.wavePlan = [];
      for (let i = 0; i < wave.total; i++) {
        this.wavePlan.push(wave.pool[Math.floor(Math.random() * wave.pool.length)]);
      }
      this.shuffle(this.wavePlan);
      this.waveSpawned = 0;
      this.currentWaveStarted = false;
      this.waveCooldown = 0;
    }
    if (this.state === 'playing') this.runWave(dt);

    for (let i = this.suns.length - 1; i >= 0; i--) {
      if (this.suns[i].y > BOARD_H + 60) this.suns.splice(i, 1);
    }

    for (const pl of this.plants) pl.update(this, dt);

    for (let i = this.projectiles.length - 1; i >= 0; i--) {
      if (this.projectiles[i].update(this, dt)) this.projectiles.splice(i, 1);
    }

    for (let i = this.zombies.length - 1; i >= 0; i--) {
      const z = this.zombies[i];
      z.update(this, dt);
      if (z.dead || z.hp <= 0) {
        if (z.hp <= 0) {
          this.sun += 20;
          this.log(`僵尸被消灭，奖金+20`);
        }
        this.zombies.splice(i, 1);
      }
    }

    for (let i = this.plants.length - 1; i >= 0; i--) {
      const p = this.plants[i];
      if (p.hp <= 0 || p.dieSoon) {
        this.plants.splice(i, 1);
      }
    }

    this.suns = this.suns.filter((s) => !s.collected);
    this.plants.forEach(p => p.update(this, 0));

    for (const s of this.suns) s.update(dt);

    if (this.lives <= 0) {
      this.state = 'lose';
      this.log('房屋被入侵，游戏失败');
    }

    this.updateCardStates();
    this.updateStatusText();
  }

  cleanupAfterLoseOrWin() {
    this.paused = true;
    pauseBtn.textContent = '暂停/继续';
  }

  draw() {
    // background
    const bg = ctx.createLinearGradient(0, 0, 0, BOARD_H);
    bg.addColorStop(0, '#2d8032');
    bg.addColorStop(1, '#1c5c27');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, BOARD_W, BOARD_H);

    // lanes + soil
    for (let r = 0; r < ROWS; r++) {
      ctx.fillStyle = r % 2 === 0 ? '#4fb55d55' : '#3d8e4c55';
      ctx.fillRect(0, r * CELL_H, BOARD_W, CELL_H);
      for (let c = 0; c <= COLS; c++) {
        ctx.strokeStyle = '#2d5c2f99';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(c * CELL_W, r * CELL_H);
        ctx.lineTo(c * CELL_W, r * CELL_H + CELL_H);
        ctx.stroke();
      }
      ctx.strokeStyle = '#2d5c2f';
      ctx.beginPath();
      ctx.moveTo(0, r * CELL_H);
      ctx.lineTo(BOARD_W, r * CELL_H);
      ctx.stroke();
    }
    ctx.strokeStyle = '#2d5c2f';
    ctx.beginPath();
    ctx.moveTo(BOARD_W, 0);
    ctx.lineTo(BOARD_W, BOARD_H);
    ctx.stroke();

    // house indicator
    ctx.fillStyle = 'rgba(80, 45, 20, 0.85)';
    ctx.fillRect(0, 0, 20, BOARD_H);

    // entities
    this.suns.forEach((s) => s.render(ctx));
    this.plants.forEach((p) => p.render(ctx));
    this.projectiles.forEach((pr) => pr.render(ctx));
    this.zombies.forEach((z) => z.render(ctx));

    // selected indicator
    if (this.selected) {
      const now = this.time;
      const cd = Math.max(0, (PLANT_TYPES[this.selected].cooldown - (now - (this.lastUsed[this.selected] || now)) ) / 1000);
      if (cd > 0) {
        ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
        ctx.fillRect(0, 0, BOARD_W, BOARD_H);
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 22px sans-serif';
        ctx.fillText(`等待冷却 ${cd.toFixed(1)}s`, BOARD_W * 0.16, BOARD_H * 0.48);
      }
    }

    // side game info and message
    ctx.fillStyle = '#15361f';
    ctx.fillRect(BOARD_W, 0, 220, BOARD_H);
    ctx.fillStyle = '#e9ffde';
    ctx.font = '20px sans-serif';
    ctx.fillText('场景预览', BOARD_W + 62, 32);
    if (this.state !== 'playing') {
      ctx.fillStyle = 'rgba(0,0,0,0.58)';
      ctx.fillRect(BOARD_W + 12, 60, 196, 140);
      ctx.fillStyle = '#fff2b8';
      ctx.font = 'bold 28px sans-serif';
      ctx.fillText(this.state === 'win' ? 'YOU WIN' : 'GAME OVER', BOARD_W + 26, 130);
      ctx.font = '16px sans-serif';
      ctx.fillText('点击重新开始', BOARD_W + 30, 165);
    }
  }

  shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  loop(ts) {
    const dt = this.lastTs ? Math.min(90, ts - this.lastTs) : 16;
    this.lastTs = ts;

    if (this.state !== 'playing') {
      if (this.state === 'lose' || this.state === 'win') this.cleanupAfterLoseOrWin();
      this.draw();
      requestAnimationFrame(this.loop.bind(this));
      return;
    }

    this.update(dt);
    this.draw();
    requestAnimationFrame(this.loop.bind(this));
  }
}

const game = new Game();

canvas.addEventListener('click', (e) => {
  game.handleClick(e);
});

pauseBtn.addEventListener('click', () => {
  if (game.state !== 'playing') return;
  game.paused = !game.paused;
  pauseBtn.textContent = game.paused ? '▶ 继续' : '⏸ 暂停/继续';
});

restartBtn.addEventListener('click', () => {
  game.reset();
  game.renderCards();
});

eraseBtn.addEventListener('click', () => {
  game.eraseMode = !game.eraseMode;
  game.selected = null;
  game.updateCardStates();
});

clearBtn.addEventListener('click', () => {
  game.clearAll();
});
