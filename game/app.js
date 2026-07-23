(() => {
  'use strict';
  const canvas = document.getElementById('gameCanvas');
  const ctx = canvas.getContext('2d');

  const W = canvas.width;
  const H = canvas.height;

  const grid = {
    x: 120,
    y: 80,
    cols: 9,
    rows: 5,
    cw: 80,
    ch: 80,
  };
  const gardenW = grid.cols * grid.cw;

  const ui = {
    plants: [...document.querySelectorAll('[data-plant]')],
    shovel: document.getElementById('btnShovel'),
    start: document.getElementById('btnStart'),
    pause: document.getElementById('btnPause'),
    reset: document.getElementById('btnReset'),
    diffs: [...document.querySelectorAll('[data-diff]')],
    hint: document.getElementById('hint'),
    sun: document.getElementById('sun'),
    lives: document.getElementById('lives'),
    wave: document.getElementById('wave'),
    score: document.getElementById('score'),
    difficulty: document.getElementById('difficulty'),
    weather: document.getElementById('weather'),
    combo: document.getElementById('combo'),
    nextWave: document.getElementById('nextWave'),
  };

  const DIFFS = {
    easy: {label: '简单', sunMul: 1.15, hpMul: 0.85, speedMul: 0.92, waveScale: 0.8, rewardMul: 0.9, startSun: 180, lives: 7},
    normal: {label: '普通', sunMul: 1.0, hpMul: 1.08, speedMul: 1.0, waveScale: 1.0, rewardMul: 1.0, startSun: 150, lives: 5},
    hard: {label: '困难', sunMul: 0.75, hpMul: 1.25, speedMul: 1.25, waveScale: 1.28, rewardMul: 1.2, startSun: 110, lives: 4},
  };

  const WEATHER_CATALOG = [
    { key: 'sunBurst', label: '阳光暴雨', duration: 9, weather: { zombieSpeed: 1.0, sunDrop: 1.6, cooldown: 0.95 } },
    { key: 'mist', label: '阴霾', duration: 9, weather: { zombieSpeed: 1.35, sunDrop: 0.7, cooldown: 1.24 } },
    { key: 'freeze', label: '寒潮', duration: 9, weather: { zombieSpeed: 0.8, sunDrop: 1.05, cooldown: 1.2 } },
    { key: 'storm', label: '风暴', duration: 10, weather: { zombieSpeed: 1.3, sunDrop: 0.55, cooldown: 1.24 } },
    { key: 'night', label: '黑雾', duration: 10, weather: { zombieSpeed: 1.2, sunDrop: 0.75, cooldown: 1.35 } },
  ];

  const plantCfg = {
    sunflower: {
      name: '向日葵', cost: 50, hp: 80, sunInterval: 6, sunValue: 25, levelCostMul: 1, maxLevel: 4, cooldown: 0,
    },
    peashooter: {
      name: '豌豆射手', cost: 100, hp: 95, fire: 1.0, dmg: 22, maxLevel: 4,
    },
    frost: {
      name: '寒冰射手', cost: 150, hp: 90, fire: 1.05, dmg: 18, slow: 0.45, slowTime: 3.0, maxLevel: 4,
    },
    cherry: {
      name: '樱桃炸弹', cost: 190, hp: 60, fire: 5.5, dmg: 120, radius: 72, maxLevel: 1,
    },
    wallnut: {
      name: '坚果', cost: 60, hp: 450, fire: 0, maxLevel: 2,
    },
    cannon: {
      name: '火炮', cost: 230, hp: 130, fire: 2.2, dmg: 80, splash: 62, maxLevel: 3,
    },
    twin: {
      name: '双发射手', cost: 280, hp: 110, fire: 0.7, dmg: 16, maxLevel: 3,
    },
  };

  const zombieTypes = [
    { key: 'walker', name: '普通僵尸', hp: 120, speed: 34, armor: 0, dmg: 1, reward: 6, spawn: 46, color: '#5bc27d' },
    { key: 'fast', name: '匆忙僵尸', hp: 96, speed: 53, armor: 0, dmg: 1, reward: 5, spawn: 18, color: '#8dcf57' },
    { key: 'cone', name: '路障僵尸', hp: 215, speed: 27, armor: 70, dmg: 1, reward: 10, spawn: 13, color: '#f8c35a' },
    { key: 'bucket', name: '铁桶僵尸', hp: 250, speed: 22, armor: 130, dmg: 1, reward: 14, spawn: 8, color: '#d9b07e' },
    { key: 'jumper', name: '跳跃僵尸', hp: 165, speed: 24, armor: 30, dmg: 2, reward: 12, spawn: 10, color: '#a67adf', special: 'jumper' },
    { key: 'flying', name: '飞行僵尸', hp: 128, speed: 56, armor: 0, dmg: 1, reward: 11, spawn: 9, color: '#72b4ff', special: 'flying' },
    { key: 'split', name: '分裂僵尸', hp: 280, speed: 24, armor: 90, dmg: 1, reward: 16, spawn: 6, color: '#b36c86', special: 'split' },
    { key: 'garg', name: '巨人僵尸', hp: 520, speed: 16, armor: 230, dmg: 2, reward: 28, isBoss: true, spawn: 4, color: '#8c6a5a' },
    { key: 'doom', name: '末日僵尸', hp: 700, speed: 14, armor: 280, dmg: 3, reward: 44, isBoss: true, spawn: 2, color: '#70507e', special: 'doom' },
  ];

  const CELL_CENTER_X = (c) => grid.x + c * grid.cw + grid.cw / 2;
  const CELL_CENTER_Y = (r) => grid.y + r * grid.ch + grid.ch / 2;

  let plants = [];      // {type,c,r,row,x,y,hp,maxHp,cd,level,sunTimer,dead}
  let zombies = [];     // {type,x,y,row,hp,maxHp,armor,maxArmor,speed,baseSpeed,atk,atkCd,dead,slowLeft}
  let bullets = [];     // {x,y,row,speed,dmg,slow,slowTime,dead,splash,radius,pierce,color,life}
  let suns = [];        // {x,y,vy,ttl,dead,r,v}
  let lawnMowers = [];  // {row,x,y,active,dead}
  let explosions = [];  // {x,y,radius,maxRadius,life,dead}
  let pickups = [];     // random power cards, same format as suns

  let selected = null;
  let shovelMode = false;

  let running = false;
  let gameOver = false;
  let sun = DIFFS.normal.startSun;
  let lives = 5;
  let wave = 0;
  let score = 0;
  let combo = 0;
  let comboRemain = 0;
  let gameClock = 0;

  let spawnRemain = 0;
  let spawnInterval = 1.2;
  let spawnTimer = 0;
  let nextWaveTimer = 1.5;
  let bossQueue = 0;

  let skySunTimer = 0;
  let weather = {key: 'normal', label: '平静', remain: 0, zombieSpeed: 1, sunDrop: 1, cooldown: 1};
  let weatherWait = 12 + Math.random() * 16;

  let lastTs = 0;

  const setHint = (txt) => {
    ui.hint.textContent = txt;
  };

  const setText = (el, txt) => {
    if (el) el.textContent = txt;
  };

  const syncDifficultyControls = () => {
    if (!ui.difficulty) return;

    if (ui.difficulty.tagName === 'SELECT') {
      ui.difficulty.value = currentDiff;
      return;
    }

    ui.diffs.forEach((btn) => setActiveClass(btn, btn.dataset.diff === currentDiff));
  };

  const setActiveClass = (el, active) => {
    if (el) el.classList.toggle('active', active);
  };

  const sync = () => {
    setText(ui.sun, Math.floor(sun));
    setText(ui.lives, lives);
    setText(ui.wave, wave);
    setText(ui.score, score);
    syncDifficultyControls();
    setText(ui.weather, weather.label);
    setText(ui.combo, combo > 1 ? combo : '1');
    setText(
      ui.nextWave,
      spawnRemain > 0
        ? `剩余僵尸 ${spawnRemain}`
        : `下一波 ${Math.max(0, nextWaveTimer).toFixed(1)}秒`
    );
  };

  const setActive = () => {
    ui.plants.forEach((btn) =>
      setActiveClass(btn, selected === btn.dataset.plant && !shovelMode)
    );
    setActiveClass(ui.shovel, shovelMode);
  };

  const changeDifficulty = (nextDiff) => {
    if (!DIFFS[nextDiff]) return;
    currentDiff = nextDiff;
    reset();
  };

  const world = (e) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left) * (W / rect.width),
      y: (e.clientY - rect.top) * (H / rect.height),
    };
  };

  const inGarden = (p) => p.x >= grid.x && p.x <= grid.x + gardenW && p.y >= grid.y && p.y <= grid.y + grid.rows * grid.ch;

  const cellOf = (p) => ({
    c: Math.floor((p.x - grid.x) / grid.cw),
    r: Math.floor((p.y - grid.y) / grid.ch),
  });

  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

  const getActiveDiff = () => DIFFS[currentDiff];

  const occupied = (c, r) => plants.find((p) => p.c === c && p.r === r && !p.dead);

  let currentDiff = 'normal';

  const selectPlant = (type) => {
    if (!plantCfg[type]) return;
    shovelMode = false;
    selected = type;
    setHint(`已选择：${plantCfg[type].name}（${plantCfg[type].cost}）`);
    setActive();
  };

  const resetLawnMowers = () => {
    lawnMowers = [];
    for (let r = 0; r < grid.rows; r++) {
      lawnMowers.push({
        row: r,
        x: grid.x - grid.cw * 0.4,
        y: grid.y + r * grid.ch + LAWN_Y_OFFSET,
        active: false,
        dead: false,
        speed: 520,
        startX: grid.x - grid.cw * 0.4,
      });
    }
  };

  const LAWN_Y_OFFSET = 24;

  const reset = () => {
    plants = [];
    zombies = [];
    bullets = [];
    suns = [];
    explosions = [];
    pickups = [];
    running = false;
    gameOver = false;
    selected = null;
    shovelMode = false;
    sun = DIFFS[currentDiff].startSun;
    lives = DIFFS[currentDiff].lives;
    wave = 0;
    score = 0;
    combo = 0;
    comboRemain = 0;
    spawnRemain = 0;
    spawnTimer = 0;
    nextWaveTimer = 1.5;
    bossQueue = 0;
    skySunTimer = 5;
    weather = {key: 'normal', label: '平静', remain: 0, zombieSpeed: 1, sunDrop: 1, cooldown: 1};
    weatherWait = 12 + Math.random() * 16;
    resetLawnMowers();
    setHint('点击植物按钮后在草坪种植，点击阳光拾取；再次选择同类植物可升级（最高级）。');
    sync();
    setActive();
    spawnSkySun();
    draw();
  };

  const spawnSkySun = () => {
    const r = 18;
    suns.push({
      x: grid.x + r + Math.random() * (gardenW - 2 * r),
      y: -r,
      vy: 40 + Math.random() * 45,
      ttl: 11 + Math.random() * 4,
      life: 0,
      dead: false,
      r,
      v: 12,
      value: 25,
    });
  };

  const randomPlantValue = () => Math.floor(15 + Math.random() * 20);

  const spawnPickup = () => {
    if (Math.random() > 0.003) return;
    const rows = Math.floor(Math.random() * grid.rows);
    pickups.push({
      x: grid.x + Math.random() * gardenW,
      y: CELL_CENTER_Y(rows) - 20,
      w: 52,
      h: 28,
      dead: false,
      ttl: 12,
      life: 0,
      type: Math.random() > 0.5 ? 'reward' : 'damage',
    });
  };

  const isBossWave = (w) => w > 0 && w % 8 === 0;

  const pickZombieType = () => {
    let pool = zombieTypes;
    if (bossQueue <= 0) {
      pool = zombieTypes.filter((t) => !t.isBoss);
    }
    const total = pool.reduce((sum, t) => sum + t.spawn, 0);
    let p = Math.random() * total;
    for (const t of pool) {
      p -= t.spawn;
      if (p <= 0) return t;
    }
    return pool[0];
  };

  const spawnZombie = () => {
    const row = (Math.random() * grid.rows) | 0;
    const isBoss = bossQueue > 0;
    const type = isBoss ? zombieTypes.find((z) => z.isBoss) : pickZombieType();
    if (isBoss && bossQueue > 0) bossQueue -= 1;

    const diff = getActiveDiff();
    const hp = (type.hp * (1 + wave * 0.055 * diff.hpMul) * (isBoss ? 1.4 : 1));
    const speed = type.speed * diff.speedMul * weather.zombieSpeed;
    const x = grid.x + gardenW + 20 + Math.random() * 50;
    const y = CELL_CENTER_Y(row);

    zombies.push({
      type: type.key,
      x,
      y,
      row,
      hp,
      maxHp: hp,
      armor: type.armor,
      maxArmor: type.armor,
      speed: speed,
      baseSpeed: speed,
      atk: type.dmg,
      attackCD: 0,
      atkInterval: 1.0,
      dead: false,
      slowLeft: 0,
      reward: type.reward,
      color: type.color,
      isBoss: !!type.isBoss,
      w: type.isBoss ? 70 : 56,
    });
  };

  const beginNextWave = () => {
    wave += 1;
    const diff = getActiveDiff();
    spawnRemain = Math.max(5, Math.floor((7 + wave * 1.8) * diff.waveScale));
    spawnInterval = clamp(1.4 - wave * 0.06, 0.55, 1.4) / diff.speedMul;
    spawnTimer = 1.1;
    nextWaveTimer = 0;
    bossQueue = isBossWave(wave) ? 1 : 0;
    setHint(`第 ${wave} 波开始，目标僵尸 ${spawnRemain} 只`);
  };

  const applyDamageZombie = (z, dmg) => {
    if (z.armor > 0) {
      const hitArmor = Math.min(z.armor, dmg);
      z.armor -= hitArmor;
      dmg -= hitArmor;
    }
    if (dmg <= 0) return;
    z.hp = clamp(z.hp - dmg, 0, z.maxHp);
    if (z.hp <= 0) {
      z.dead = true;
      const rewardBase = (z.isBoss ? 2 : 1) * z.reward * getActiveDiff().rewardMul;
      const reward = Math.floor(rewardBase + combo * 2);
      score += reward;
      combo = Math.min(combo + 1, 14);
      comboRemain = 2.5;
      explosions.push({
        x: z.x,
        y: z.y,
        radius: 0,
        maxRadius: 38,
        life: 0.35,
        dead: false,
      });
      sun += 8 + (z.isBoss ? 20 : 0);
    }
  };

  const explodeCherry = (p) => {
    const cfg = plantCfg.cherry;
    explosions.push({
      x: p.x,
      y: p.y,
      radius: 0,
      maxRadius: cfg.radius,
      life: 0.55,
      dead: false,
    });

    const base = cfg.dmg;
    zombies.forEach((z) => {
      if (z.dead) return;
      const d = Math.hypot(z.x - p.x, z.y - p.y);
      if (d <= cfg.radius) {
        const rate = (1 - d / cfg.radius) ** 1.2;
        applyDamageZombie(z, Math.floor(base * rate));
      }
    });
    setHint('樱桃炸弹引爆！');
  };

  const findFrontZombie = (plant) => {
    let target = null;
    let minX = 1e9;
    for (const z of zombies) {
      if (z.dead || z.row !== plant.row) continue;
      if (z.x < plant.x) continue;
      if (z.x < minX) {
        minX = z.x;
        target = z;
      }
    }
    return target;
  };

  const frontPlant = (z) => {
    let target = null;
    let maxX = -1;
    for (const p of plants) {
      if (p.dead || p.row !== z.row || p.x >= z.x) continue;
      if (p.x > maxX) {
        maxX = p.x;
        target = p;
      }
    }
    return target;
  };

  const pickupValue = (obj) => {
    if (obj.type === 'reward') {
      const add = 40 + Math.floor(Math.random() * 30);
      sun += add;
      explosions.push({ x: obj.x, y: obj.y, radius: 0, maxRadius: 26, life: 0.2, dead: false });
      setHint(`拾取特殊卡牌：+${add} 阳光`);
    } else {
      const damage = 25 + Math.floor(Math.random() * 35);
      explosions.push({ x: obj.x, y: obj.y, radius: 0, maxRadius: 26, life: 0.2, dead: false });
      setHint(`拾取攻击卡牌：下路僵尸将受${damage}伤害`);
      zombies.forEach((z) => {
        if (!z.dead) applyDamageZombie(z, damage * 0.15);
      });
    }
    obj.dead = true;
  };

  const createBullet = (plant, cfg, dmgMul = 1) => {
    const speed = 470 * cfg.speedMul || 470;
    const b = {
      x: plant.x + 22,
      y: plant.y - 2,
      row: plant.row,
      speed,
      dmg: Math.round((cfg.dmg || 0) * dmgMul),
      slow: cfg.slow || 0,
      slowTime: cfg.slowTime || 0,
      radius: 7,
      dead: false,
      splash: cfg.splash || 0,
      pierce: false,
      color: '#fff2b1',
      life: 0,
      maxLife: 4,
    };
    bullets.push(b);
  };

  const updateMowers = (dt) => {
    for (const m of lawnMowers) {
      if (!m.active) continue;
      m.x += m.speed * dt;
      for (const z of zombies) {
        if (z.dead || z.row !== m.row) continue;
        if (z.x + z.w / 2 >= m.x - 8 && z.x - z.w / 2 <= m.x + 32) {
          z.dead = true;
          score += 8;
          sun += 2;
        }
      }
      if (m.x > grid.x + gardenW + 12) {
        m.active = false;
        m.x = m.startX;
      }
    }
  };

  const triggerMower = (row) => {
    const m = lawnMowers[row];
    if (m && !m.active) {
      m.active = true;
      setHint('草坪车已启动，清理该行！');
    }
  };

  const update = (dt) => {
    if (!running || gameOver) {
      sync();
      draw();
      return;
    }

    if (combo > 0) {
      comboRemain -= dt;
      if (comboRemain <= 0) combo = 0;
    }

    const diff = getActiveDiff();

    weatherWait -= dt;
    if (weatherWait <= 0 && weather.remain <= 0) {
      const evt = WEATHER_CATALOG[(Math.random() * WEATHER_CATALOG.length) | 0];
      weather = {
        key: evt.key,
        label: evt.label,
        remain: evt.duration,
        zombieSpeed: evt.weather.zombieSpeed,
        sunDrop: evt.weather.sunDrop,
        cooldown: evt.weather.cooldown,
      };
      weatherWait = 16 + Math.random() * 16;
      setHint(`环境事件：${evt.label} 生效中`);
    }
    if (weather.remain > 0) {
      weather.remain -= dt;
      if (weather.remain <= 0) {
        weather = {key: 'normal', label: '平静', remain: 0, zombieSpeed: 1, sunDrop: 1, cooldown: 1};
        setHint('环境恢复平静。');
      }
    }

    // Waves
    if (spawnRemain > 0) {
      spawnTimer -= dt;
      while (spawnRemain > 0 && spawnTimer <= 0) {
        spawnZombie();
        spawnRemain -= 1;
        spawnTimer += spawnInterval;
      }
    } else if (zombies.length === 0) {
      if (nextWaveTimer <= 0) {
        beginNextWave();
      } else {
        nextWaveTimer -= dt;
      }
    }

    // Sunny rain
    const sunDrop = (3.1 / (diff.sunMul * weather.sunDrop));
    skySunTimer -= dt;
    if (skySunTimer <= 0) {
      spawnSkySun();
      skySunTimer = sunDrop;
    }

    // power pickups spawn
    spawnPickup();

    // update plants
    for (const p of plants) {
      if (p.dead) continue;
      if (p.cd > 0) p.cd -= dt * weather.cooldown;
      if (p.type === 'sunflower') {
        p.sunTimer -= dt;
        if (p.sunTimer <= 0) {
          suns.push({
            x: p.x,
            y: p.y,
            vy: 18 + 10 * Math.random(),
            ttl: 6,
            life: 0,
            dead: false,
            r: 16,
            v: 9,
            value: plantCfg.sunflower.sunValue + p.level * 5,
          });
          p.sunTimer = plantCfg.sunflower.sunInterval;
        }
        continue;
      }

      const cfg = plantCfg[p.type];
      if (!cfg || cfg.fire === 0) continue;

      const target = findFrontZombie(p);
      if (!target) continue;
      if (p.cd > 0) continue;

      const shootMul = 1 + (p.level - 1) * 0.22;

      if (p.type === 'cherry') {
        explodeCherry(p);
        p.cd = cfg.fire;
        p.dead = true;
        continue;
      }

      if (p.type === 'cannon') {
        createBullet(p, {...cfg, dmg: Math.round(cfg.dmg * shootMul), speedMul: 0.66, splash: cfg.splash}, 1);
        p.cd = cfg.fire;
        continue;
      }

      if (p.type === 'twin') {
        createBullet(p, {...cfg, dmg: Math.round(cfg.dmg * 0.75 * shootMul)}, 0.45);
        createBullet({ ...p, x: p.x - 2 }, {...cfg, dmg: Math.round(cfg.dmg * 0.75 * shootMul)}, 0.45);
        p.cd = cfg.fire;
        continue;
      }

      createBullet(p, {...cfg, dmg: Math.round((cfg.dmg || 0) * shootMul)});
      p.cd = cfg.fire;
    }

    // update bullets
    for (const b of bullets) {
      if (b.dead) continue;
      b.x += b.speed * dt;
      b.life += dt;
      if (b.x > W + 20 || b.life > b.maxLife) b.dead = true;

      for (const z of zombies) {
        if (z.dead || z.row !== b.row) continue;
        if (b.x < z.x - z.w / 2 || b.x > z.x + z.w / 2) continue;
        if (Math.abs(b.y - z.y) > z.w / 2) continue;

        applyDamageZombie(z, b.dmg);

        if (b.slow > 0) {
          z.slowLeft = Math.max(z.slowLeft, b.slowTime);
        }

        if (b.splash > 0) {
          for (const aoe of zombies) {
            if (aoe.dead || aoe.row !== z.row || aoe === z) continue;
            const d = Math.hypot(aoe.x - b.x, aoe.y - b.y);
            if (d <= b.splash) {
              applyDamageZombie(aoe, Math.round(b.dmg * (1 - d / b.splash)));
            }
          }
        }

        if (!b.pierce) b.dead = true;
        break;
      }
    }

    // update zombies
    for (const z of zombies) {
      if (z.dead) continue;
      if (z.slowLeft > 0) z.slowLeft = Math.max(0, z.slowLeft - dt);

      const target = frontPlant(z);
      if (target) {
        z.attackCD -= dt;
        if (z.attackCD <= 0) {
          target.hp -= z.atk;
          z.attackCD = z.atkInterval;
          if (target.hp <= 0) target.dead = true;
        }
      } else {
        z.x -= z.baseSpeed * diff.speedMul * weather.zombieSpeed * (z.slowLeft > 0 ? 0.45 : 1) * dt;
      }

      if (z.x < grid.x + LAWN_TRIGGER_X) {
        const row = z.row;
        triggerMower(row);
      }

      if (z.x <= grid.x - 8) {
        z.dead = true;
        lives -= 1;
        setHint('僵尸突破草坪，生命-1！');
        if (lives <= 0) {
          gameOver = true;
          running = false;
          setHint('游戏结束！点击【重置】后可再次挑战。');
        }
      }
    }

    // update lawn mowers
    updateMowers(dt);

    // update sky/ground suns
    for (const s of suns) {
      if (s.dead) continue;
      s.life += dt;
      s.y += s.vy * dt;
      if (s.life > s.ttl) s.dead = true;
    }

    // update explosions
    for (const e of explosions) {
      if (e.dead) continue;
      e.life -= dt;
      if (e.life <= 0) e.dead = true;
    }

    // update pickups
    for (const p of pickups) {
      if (p.dead) continue;
      p.life += dt;
      if (p.life > p.ttl) p.dead = true;
    }

    // cleanup
    plants = plants.filter((p) => !p.dead);
    bullets = bullets.filter((b) => !b.dead);
    zombies = zombies.filter((z) => !z.dead);
    suns = suns.filter((s) => !s.dead);
    explosions = explosions.filter((e) => !e.dead);
    pickups = pickups.filter((p) => !p.dead);

    sync();
    draw();
  };

  const removeDeadPlants = () => {
    for (const p of plants) {
      if (p.hp <= 0) p.dead = true;
    }
  };

  const drawGrid = () => {
    ctx.save();
    ctx.strokeStyle = 'rgba(66, 58, 24, 0.6)';
    ctx.lineWidth = 2;

    for (let r = 0; r <= grid.rows; r++) {
      const y = grid.y + r * grid.ch;
      ctx.beginPath();
      ctx.moveTo(grid.x, y);
      ctx.lineTo(grid.x + gardenW, y);
      ctx.stroke();
    }

    for (let c = 0; c <= grid.cols; c++) {
      const x = grid.x + c * grid.cw;
      ctx.beginPath();
      ctx.moveTo(x, grid.y);
      ctx.lineTo(x, grid.y + grid.rows * grid.ch);
      ctx.stroke();
    }
    ctx.restore();
  };

  const draw = () => {
    ctx.clearRect(0, 0, W, H);

    // grass field
    const g1 = ctx.createLinearGradient(0, 0, 0, H);
    g1.addColorStop(0, '#5bb85a');
    g1.addColorStop(1, '#3e9b45');
    ctx.fillStyle = g1;
    ctx.fillRect(0, 0, W, H);

    // lane background and path
    for (let r = 0; r < grid.rows; r++) {
      const y = grid.y + r * grid.ch;
      ctx.fillStyle = r % 2 === 0 ? 'rgba(84, 163, 86, 0.95)' : 'rgba(76, 152, 78, 0.93)';
      ctx.fillRect(grid.x, y + 12, gardenW, grid.ch - 24);
    }

    drawGrid();

    // pickups
    for (const p of pickups) {
      ctx.save();
      const alpha = p.life < 0.5 ? p.life / 0.5 : 1;
      ctx.globalAlpha = Math.min(1, alpha);
      const cx = p.x;
      const cy = p.y;
      if (p.type === 'reward') {
        ctx.fillStyle = '#ffd54f';
      } else {
        ctx.fillStyle = '#ff8585';
      }
      ctx.fillRect(cx - 18, cy - 10, p.w, p.h);
      ctx.fillStyle = '#2d1703';
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(p.type === 'reward' ? '阳' : '攻', cx + 8, cy + 8);
      ctx.restore();
    }

    // suns
    for (const s of suns) {
      ctx.beginPath();
      ctx.fillStyle = '#f4e542';
      ctx.ellipse(s.x, s.y, s.r, s.r * 0.64, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#d4b82b';
      ctx.beginPath();
      ctx.arc(s.x, s.y, 4, 0, Math.PI * 2);
      ctx.fill();
    }

    // explosions
    for (const e of explosions) {
      const p = 1 - e.life / 0.55;
      const r = e.maxRadius * p;
      ctx.strokeStyle = `rgba(255, 180, 35, ${1 - p})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(e.x, e.y, r, 0, Math.PI * 2);
      ctx.stroke();
    }

    // bullets
    for (const b of bullets) {
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.ellipse(b.x, b.y, b.radius, b.radius * 0.6, 0, 0, Math.PI * 2);
      ctx.fill();
    }

    // plants
    for (const p of plants) {
      const cfg = plantCfg[p.type];
      const w = grid.cw * 0.58;
      const h = grid.ch * 0.58;
      const x = p.x - w / 2;
      const y = p.y - h / 2;

      if (p.type === 'sunflower') ctx.fillStyle = '#ffef8a';
      else if (p.type === 'peashooter' || p.type === 'twin') ctx.fillStyle = '#80d8ff';
      else if (p.type === 'frost') ctx.fillStyle = '#92ddff';
      else if (p.type === 'cherry') ctx.fillStyle = '#ff8b8b';
      else if (p.type === 'cannon') ctx.fillStyle = '#ffb347';
      else if (p.type === 'wallnut') ctx.fillStyle = '#c49c64';
      else ctx.fillStyle = '#ffffff';

      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = '#3a2a14';
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, w, h);

      if (p.type === 'sunflower') {
        ctx.fillStyle = '#f7c94d';
        ctx.beginPath();
        ctx.arc(p.x, p.y, 14, 0, Math.PI * 2);
        ctx.fill();
      }

      if (cfg.maxLevel > 1) {
        const txt = `${p.level}/${cfg.maxLevel}`;
        ctx.fillStyle = '#222';
        ctx.font = 'bold 12px sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(txt, x + w - 4, y + 14);
      }

      ctx.fillStyle = '#ffffff';
      ctx.fillStyle = '#202020';
      ctx.font = 'bold 11px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(cfg.name?.slice(0, 3) || p.type, p.x, y + h + 13);

      const hpRate = p.hp / p.maxHp;
      ctx.fillStyle = '#111';
      ctx.fillRect(x, y + h + 2, w, 4);
      ctx.fillStyle = hpRate > 0.6 ? '#5fd35f' : hpRate > 0.3 ? '#f5d14f' : '#f26c6c';
      ctx.fillRect(x, y + h + 2, w * hpRate, 4);
    }

    // zombies
    for (const z of zombies) {
      const w = z.w;
      const h = 42;
      ctx.fillStyle = z.color || '#a5c56c';
      ctx.fillRect(z.x - w / 2, z.y - h / 2, w, h);
      ctx.fillStyle = '#7a4c2a';
      ctx.fillRect(z.x - 4, z.y - h / 2 - 8, 8, 8);

      const hpRate = z.hp / z.maxHp;
      const arRate = z.armor > 0 ? z.armor / z.maxArmor : 0;
      ctx.fillStyle = '#111';
      ctx.fillRect(z.x - w / 2, z.y - h / 2 - 12, w, 4);
      ctx.fillStyle = hpRate > 0.55 ? '#4ec14e' : hpRate > 0.25 ? '#dab13d' : '#d65d54';
      ctx.fillRect(z.x - w / 2, z.y - h / 2 - 12, w * hpRate, 4);

      if (arRate > 0) {
        ctx.fillStyle = '#7f7f7f';
        ctx.fillRect(z.x - w / 2, z.y - h / 2 - 18, w * arRate, 3);
      }

      if (z.isBoss) {
        ctx.strokeStyle = '#ffe66f';
        ctx.strokeRect(z.x - w / 2 - 3, z.y - h / 2 - 3, w + 6, h + 6);
      }
    }

    // lawn mowers
    for (const m of lawnMowers) {
      if (!m.active) {
        ctx.strokeStyle = '#6b4019';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(m.startX, m.y + 8);
        ctx.lineTo(m.startX + 4, m.y + 8);
        ctx.stroke();
      } else {
        ctx.fillStyle = '#fff5b9';
        ctx.fillRect(m.x - 2, m.y - 8, 42, 16);
        ctx.fillStyle = '#9b6420';
        ctx.fillRect(m.x + 10, m.y - 10, 10, 20);
      }
    }

    // combo and weather hints
    ctx.fillStyle = 'rgba(0,0,0,0.35)';
    ctx.fillRect(16, 8, 320, 64);
    ctx.fillStyle = '#f7fcf2';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(`模式: ${DIFFS[currentDiff].label}`, 24, 24);
    ctx.fillText(`环境: ${weather.label}`, 24, 44);
    if (combo > 1) {
      ctx.fillStyle = '#ffef7d';
      ctx.fillText(`连击 x${combo}`, 24, 64);
    }
  };

  const handleClick = (e) => {
    const p = world(e);

    for (const s of suns) {
      if (s.dead) continue;
      const d = Math.hypot(s.x - p.x, s.y - p.y);
      if (d <= s.r) {
        sun += s.value;
        s.dead = true;
        setHint(`拾取阳光 +${s.value}`);
        sync();
        draw();
        return;
      }
    }

    for (const pku of pickups) {
      if (pku.dead) continue;
      if (Math.abs(pku.x - p.x) <= pku.w / 2 && Math.abs(pku.y - p.y) <= pku.h / 2) {
        pickupValue(pku);
        sync();
        draw();
        return;
      }
    }

    if (!inGarden(p)) return;

    const { c, r } = cellOf(p);
    if (c < 0 || c >= grid.cols || r < 0 || r >= grid.rows) return;

    const cellPlant = occupied(c, r);
    const cx = CELL_CENTER_X(c);
    const cy = CELL_CENTER_Y(r);

    if (shovelMode) {
      if (cellPlant) {
        cellPlant.dead = true;
        setHint('已清除植物。');
      } else {
        setHint('这里什么都没有。');
      }
      shovelMode = false;
      selected = null;
      setActive();
      return;
    }

    if (!selected) {
      setHint('请先选择一种植物。');
      return;
    }

    const cfg = plantCfg[selected];
    if (!cfg) return;

    if (cellPlant) {
      if (cellPlant.type !== selected) {
        setHint('该格已被其他植物占据，无法放置。');
        return;
      }

      const nextLevel = Math.min(cellPlant.level + 1, cfg.maxLevel);
      if (nextLevel <= cellPlant.level) {
        setHint('该植物已满级。');
        return;
      }
      const cost = Math.floor(cfg.cost * (0.65 + nextLevel * 0.45));
      if (sun < cost) {
        setHint(`阳光不足，升级需要 ${cost}`);
        return;
      }

      sun -= cost;
      cellPlant.level = nextLevel;
      const oldHP = cellPlant.maxHp;
      cellPlant.maxHp = Math.floor(oldHP * 1.35);
      cellPlant.hp = cellPlant.maxHp;
      cellPlant.cd = Math.max(0, cellPlant.cd * 0.92);
      setHint(`升级成功：${cfg.name} -> Lv.${cellPlant.level}`);
      sync();
      draw();
      return;
    }

    if (sun < cfg.cost) {
      setHint(`阳光不足，${cfg.name} 需要 ${cfg.cost}`);
      return;
    }

    sun -= cfg.cost;
    plants.push({
      type: selected,
      c,
      r,
      row: r,
      x: cx,
      y: cy,
      hp: cfg.hp,
      maxHp: cfg.hp,
      cd: 0,
      level: 1,
      dead: false,
      sunTimer: cfg.sunInterval || 0,
    });
    setHint(`已种植 ${cfg.name}（剩余阳光 ${Math.floor(sun)}）`);
    sync();
    draw();
  };

  const bindUI = () => {
    ui.plants.forEach((btn) => {
      btn.addEventListener('click', () => {
        const type = btn.dataset.plant;
        if (type) selectPlant(type);
      });
    });

    ui.shovel.addEventListener('click', () => {
      shovelMode = !shovelMode;
      selected = null;
      setHint(shovelMode ? '铲子模式：点击植物直接移除' : '已退出铲子模式');
      setActive();
    });

    ui.start.addEventListener('click', () => {
      if (gameOver) return;
      if (!running) {
        if (wave === 0) beginNextWave();
        running = true;
        setHint('游戏开始！');
        requestAnimationFrame(tick);
      }
    });

    ui.pause.addEventListener('click', () => {
      if (!running) {
        running = true;
        requestAnimationFrame(tick);
        setHint('游戏恢复');
      } else {
        running = false;
        setHint('游戏已暂停');
      }
    });

    ui.reset.addEventListener('click', reset);

    if (ui.difficulty && ui.difficulty.tagName === 'SELECT') {
      ui.difficulty.addEventListener('change', (e) => {
        changeDifficulty(e.target.value);
      });
    } else {
      ui.diffs.forEach((btn) => {
        btn.addEventListener('click', () => {
          changeDifficulty(btn.dataset.diff);
        });
      });
    }

    canvas.addEventListener('click', handleClick);
  };

  const CA = new Set();

  const tick = (ts) => {
    if (!lastTs) lastTs = ts;
    const dt = Math.min((ts - lastTs) / 1000, 0.05);
    lastTs = ts;
    gameClock += dt;

    update(dt);
    if (running && !gameOver) requestAnimationFrame(tick);
  };

  const LAWN_TRIGGER_X = 24 + grid.x;

  reset();
  bindUI();
  requestAnimationFrame(tick);
})();