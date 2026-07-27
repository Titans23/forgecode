(() => {
  'use strict';

  const canvas = document.getElementById('game-canvas');
  const ctx = canvas.getContext('2d');

  const sunDisplay = document.getElementById('sun-display');
  const scoreDisplay = document.getElementById('score-display');
  const waveDisplay = document.getElementById('wave-display');
  const livesDisplay = document.getElementById('lives-display');
  const nextWaveDisplay = document.getElementById('wave-timer');
  const selectedNameDisplay = document.getElementById('selected-name');
  const statusEl = document.getElementById('status');
  const pauseBtn = document.getElementById('pauseBtn');
  const restartBtn = document.getElementById('restartBtn');
  const shovelBtn = document.getElementById('shovelBtn');

  if (!canvas || !ctx) {
    return;
  }

  const PLANT_TYPES = {
    pea: {
      key: 'pea',
      name: 'Pea Shooter',
      cost: 100,
      color: '#67c26f',
      maxHealth: 80,
      update(state, plant, dt) {
        plant.shootCooldown -= dt;
        if (plant.shootCooldown > 0) {
          return;
        }

        if (!hasZombieAhead(state, plant)) {
          return;
        }

        state.projectiles.push({
          type: 'pea',
          row: plant.row,
          x: plant.x + 18,
          y: plant.y,
          speed: 360,
          damage: 34,
          radius: 5,
        });
        plant.shootCooldown = 1.25;
      },
    },
    sun: {
      key: 'sun',
      name: 'Sunflower',
      cost: 50,
      color: '#e6c14d',
      maxHealth: 70,
      update(state, plant, dt) {
        plant.sunCooldown -= dt;
        if (plant.sunCooldown > 0) {
          return;
        }

        spawnSunToken(
          state,
          plant.x + (Math.random() - 0.5) * state.grid.cellW * 0.4,
          state.grid.top + Math.random() * state.grid.cellH * 0.35,
          0,
          true,
        );
        plant.sunCooldown = 10;
      },
    },
    wall: {
      key: 'wall',
      name: 'Wall-Nut',
      cost: 50,
      color: '#c4a36a',
      maxHealth: 320,
      update() {},
    },
    cherry: {
      key: 'cherry',
      name: 'Cherry Bomb',
      cost: 150,
      color: '#f26d4d',
      maxHealth: 90,
      update(state, plant, dt) {
        if (plant.exploding) {
          return;
        }

        plant.delay -= dt;
        if (plant.delay > 0) {
          return;
        }

        triggerBomb(state, plant, state.grid.cellW * 1.8, 180);
        plant.exploding = true;
      },
    },
    potato: {
      key: 'potato',
      name: 'Potato Mine',
      cost: 125,
      color: '#654321',
      maxHealth: 90,
      update(state, plant, dt) {
        if (plant.armed) {
          const zombie = firstZombieInRowAhead(state, plant.row, plant.x - 8);
          if (zombie && zombie.x <= plant.x + state.grid.cellW * 0.45) {
            triggerBomb(state, plant, state.grid.cellW * 1.6, 170);
            plant.exploding = true;
          }
          return;
        }

        plant.armDelay -= dt;
        if (plant.armDelay <= 0) {
          plant.armed = true;
        }
      },
    },
  };

  const ZOMBIE_TYPES = {
    normal: {
      speed: 45,
      health: 240,
      color: '#8de7ff',
      size: 0.72,
      damagePerSecond: 26,
    },
  };

  const GRID = {
    rows: 5,
    cols: 9,
    left: 72,
    right: 48,
    top: 60,
    bottom: 52,
  };

  const state = {
    running: false,
    gameOver: false,
    paused: false,
    wave: 1,
    lives: 5,
    sun: 170,
    score: 0,
    nextPlantId: 1,
    selectedPlant: 'pea',
    board: [],
    plants: [],
    zombies: [],
    projectiles: [],
    suns: [],
    explosions: [],
    skySunTimer: 0,
    spawn: {
      remainingInWave: 0,
      accumulator: 0,
      interval: 1.4,
      cooldown: 0,
    },
    grid: {
      cellW: 0,
      cellH: 0,
    },
  };

  let lastTime = 0;

  function setStatus(message) {
    if (statusEl) {
      statusEl.textContent = message;
    }
  }

  function selectedPlantTemplate() {
    return PLANT_TYPES[state.selectedPlant];
  }

  function computeGridMetrics() {
    state.grid.cellW = (canvas.width - GRID.left - GRID.right) / GRID.cols;
    state.grid.cellH = (canvas.height - GRID.top - GRID.bottom) / GRID.rows;
  }

  function cellToWorld(row, col) {
    return {
      x: GRID.left + col * state.grid.cellW + state.grid.cellW * 0.5,
      y: GRID.top + row * state.grid.cellH + state.grid.cellH * 0.5,
 }
  }

  function worldToCell(x, y) {
    if (x < GRID.left || x > canvas.width - GRID.right || y < GRID.top || y > canvas.height - GRID.bottom) {
      return null;
    }

    const col = Math.floor((x - GRID.left) / state.grid.cellW);
    const row = Math.floor((y - GRID.top) / state.grid.cellH);

    if (col < 0 || col >= GRID.cols || row < 0 || row >= GRID.rows) {
      return null;
    }

    return { row, col };
  }

  function boardGet(row, col) {
    return state.board[row] ? state.board[row][col] : null;
  }

  function boardSet(row, col, plantId) {
    if (!state.board[row]) {
      return;
    }
    state.board[row][col] = plantId;
  }

  function buildBoard() {
    state.board = Array.from({ length: GRID.rows }, () => Array(GRID.cols).fill(null));
  }

  function resetScene() {
    state.plants = [];
    state.zombies = [];
    state.projectiles = [];
    state.suns = [];
    state.explosions = [];
    state.spawn = {
      remainingInWave: 0,
      accumulator: 0,
      interval: 1.4,
      cooldown: 0,
    };

    state.nextPlantId = 1;
    state.skySunTimer = 6 + Math.random() * 8;
    buildBoard();
  }

  function initState() {
    computeGridMetrics();
    resetScene();
    state.running = true;
    state.paused = false;
    state.gameOver = false;
    state.wave = 1;
    state.lives = 5;
    state.sun = 170;
    state.score = 0;
    if (pauseBtn) {
      pauseBtn.textContent = 'Pause';
      pauseBtn.dataset.state = 'running';
    }
    lastTime = performance.now();
    setStatus('Plant your defenses and defend the house.');
    startNextWave();
    requestAnimationFrame(gameLoop);
  }

  function startNextWave() {
    const baseZombies = 4 + state.wave * 2;
    state.spawn.remainingInWave = baseZombies + Math.floor(Math.random() * 3);
    state.spawn.accumulator = 0;
    state.spawn.interval = Math.max(0.8, 1.45 - state.wave * 0.07);
    state.spawn.cooldown = 0;
  }

  function spawnZombie() {
    const type = ZOMBIE_TYPES.normal;
    const row = Math.floor(Math.random() * GRID.rows);
    const health = type.health + state.wave * 18;
    const speed = type.speed + state.wave * 3;
    const sizeScale = type.size;
    const y = GRID.top + row * state.grid.cellH + state.grid.cellH * (1 - sizeScale) * 0.45;
    const x = canvas.width + state.grid.cellW * 0.6;

    state.zombies.push({
      id: `z-${state.wave}-${Math.random().toString(36).slice(2, 8)}`,
      type: 'normal',
      row,
      x,
      y,
      w: state.grid.cellW * sizeScale,
      h: state.grid.cellH * 0.76,
      speed,
      health,
      maxHealth: health,
      attackCooldown: 0,
      alive: true,
      damagePerSecond: type.damagePerSecond,
      color: type.color,
    });
  }

  function spawnSkySun() {
    const x = GRID.left + Math.random() * (canvas.width - GRID.left - GRID.right);
    spawnSunToken(state, x, 20, 35, false);
  }

  function spawnSunToken(stateObj, x, y, velocityY = 30, isFromPlant = false) {
    stateObj.suns.push({
      id: `sun-${Math.random().toString(36).slice(2, 8)}`,
      x,
      y,
      speed: velocityY > 0 ? velocityY : 45,
      radius: 17,
      value: 25,
      fromPlant: isFromPlant,
    });
  }

  function triggerBomb(stateObj, plant, radius, damage) {
    const idx = stateObj.plants.findIndex((candidate) => candidate.id === plant.id);
    if (idx >= 0) {
      removePlantAtIndex(idx);
    }

    stateObj.explosions.push({
      x: plant.x,
      y: plant.y,
      radius,
      life: 0.45,
      maxLife: 0.45,
    });

    stateObj.zombies.forEach((zombie) => {
      const dy = (zombie.y + zombie.h * 0.5) - (plant.y + state.grid.cellH * 0.5);
      const dx = zombie.x + zombie.w * 0.5 - plant.x;
      if (Math.abs(dx) <= radius * 0.95 && Math.abs(dy) <= radius * 0.75) {
        zombie.health -= damage;
      }
    });
  }

  function updateHUD() {
    if (sunDisplay) sunDisplay.textContent = String(Math.floor(state.sun));
    if (scoreDisplay) scoreDisplay.textContent = String(state.score);
    if (waveDisplay) waveDisplay.textContent = String(state.wave);
    if (livesDisplay) livesDisplay.textContent = String(state.lives);
    if (selectedNameDisplay) {
      const current = selectedPlantTemplate();
      selectedNameDisplay.textContent = current ? current.name : 'Shovel';
    }

    if (nextWaveDisplay) {
      if (state.spawn.cooldown > 0) {
        nextWaveDisplay.textContent = `${Math.max(0, state.spawn.cooldown).toFixed(1)}s`;
      } else {
        nextWaveDisplay.textContent = 'Now';
      }
    }
  }

  function removePlantAtIndex(index) {
    const removed = state.plants[index];
    if (!removed) {
      return;
    }
    boardSet(removed.row, removed.col, null);
    state.plants.splice(index, 1);
  }

  function removeZombie(index) {
    state.zombies.splice(index, 1);
  }

  function hasZombieAhead(stateObj, plant) {
    return stateObj.zombies.some((z) => z.row === plant.row && z.x + z.w > plant.x);
  }

  function firstZombieInRowAhead(stateObj, row, referenceX) {
    return stateObj.zombies.find((z) => z.row === row && z.x <= referenceX + 2);
  }

  function updatePlants(dt) {
    for (let i = state.plants.length - 1; i >= 0; i--) {
      const plant = state.plants[i];
      const data = PLANT_TYPES[plant.type];
      if (!data || !data.update) {
        continue;
      }

      data.update(state, plant, dt);
      if (plant.health <= 0) {
        removePlantAtIndex(i);
      }
      if (plant.exploding) {
        removePlantAtIndex(i);
      }
    }
  }

  function updateProjectiles(dt) {
    for (let i = state.projectiles.length - 1; i >= 0; i--) {
      const bullet = state.projectiles[i];
      bullet.x += bullet.speed * dt;

      if (bullet.x - bullet.radius > canvas.width) {
        state.projectiles.splice(i, 1);
        continue;
      }

      for (let z = state.zombies.length - 1; z >= 0; z--) {
        const zombie = state.zombies[z];
        if (zombie.row !== bullet.row) {
          continue;
        }

        if (bullet.x + bullet.radius >= zombie.x && bullet.x - bullet.radius <= zombie.x + zombie.w) {
          zombie.health -= bullet.damage;
          state.projectiles.splice(i, 1);
          if (zombie.health <= 0) {
            state.zombies.splice(z, 1);
            state.score += 12;
          }
          break;
        }
      }
    }
  }

  function applyZombieCellDamage(stateObj, zombie, dt) {
    const plant = plantAtZombieFront(zombie);
    if (!plant) {
      return;
    }

    zombie.attackCooldown -= dt;
    if (zombie.attackCooldown > 0) {
      zombie.x = Math.min(zombie.x, plant.x - state.grid.cellW * 0.2);
      return;
    }

    plant.health -= zombie.damagePerSecond * dt * 1.2;
    zombie.attackCooldown = 0.55;

    if (plant.health <= 0) {
      const idx = stateObj.plants.findIndex((candidate) => candidate.id === plant.id);
      if (idx >= 0) {
        removePlantAtIndex(idx);
      }
      zombie.x = plant.x + 4;
    } else {
      zombie.x = Math.min(zombie.x, plant.x - state.grid.cellW * 0.2);
    }
  }

  function plantAtZombieFront(zombie) {
    let best = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const plant of state.plants) {
      if (plant.row !== zombie.row) {
        continue;
      }
      const front = plant.x - zombie.x;
      if (front > 0 && front < bestDistance) {
        bestDistance = front;
        best = plant;
      }
    }
    return best;
  }

  function updateZombies(dt) {
    for (let i = state.zombies.length - 1; i >= 0; i--) {
      const zombie = state.zombies[i];
      const nextX = zombie.x - zombie.speed * dt;

      if (nextX + zombie.w <= GRID.left) {
        state.lives -= 1;
        state.zombies.splice(i, 1);
        continue;
      }

      zombie.x = nextX;
      applyZombieCellDamage(state, zombie, dt);

      if (zombie.health <= 0) {
        state.zombies.splice(i, 1);
        state.score += 25;
      }
    }
  }

  function updateSpawners(dt) {
    if (state.spawn.cooldown > 0) {
      state.spawn.cooldown -= dt;
      if (state.spawn.cooldown <= 0) {
        state.wave += 1;
        startNextWave();
      }
      return;
    }

    if (state.spawn.remainingInWave > 0) {
      state.spawn.accumulator += dt;
      if (state.spawn.accumulator >= state.spawn.interval) {
        state.spawn.accumulator -= state.spawn.interval;
        spawnZombie();
        state.spawn.remainingInWave -= 1;
      }
      return;
    }

    if (state.zombies.length === 0 && state.spawn.remainingInWave === 0) {
      state.spawn.cooldown = 6;
      setStatus(`Wave ${state.wave} complete. Next wave in 6s`);
    }
  }

  function updateSuns(dt) {
    for (let i = state.suns.length - 1; i >= 0; i--) {
      const sun = state.suns[i];
      sun.y += sun.speed * dt;
      if (sun.y > canvas.height + 20) {
        state.suns.splice(i, 1);
      }
    }

    state.skySunTimer -= dt;
    if (state.skySunTimer <= 0) {
      spawnSkySun();
      state.skySunTimer = 8 + Math.random() * 8;
    }
  }

  function updateExplosions(dt) {
    for (let i = state.explosions.length - 1; i >= 0; i--) {
      const boom = state.explosions[i];
      boom.life -= dt;
      if (boom.life <= 0) {
        state.explosions.splice(i, 1);
      }
    }
  }

  function gameOverCheck() {
    if (state.lives > 0) {
      return;
    }

    state.gameOver = true;
    state.running = false;
    state.paused = false;
    if (pauseBtn) {
      pauseBtn.textContent = 'Pause';
      pauseBtn.dataset.state = 'running';
    }
    setStatus(`Game Over! Final Score: ${state.score}. Press Restart to try again.`);
  }

  function cleanupDeadZombies() {
    state.zombies = state.zombies.filter((zombie) => zombie.health > 0);
  }

  function update(dt) {
    if (state.gameOver || state.paused || !state.running) {
      return;
    }

    updatePlants(dt);
    updateProjectiles(dt);
    updateZombies(dt);
    cleanupDeadZombies();
    updateSuns(dt);
    updateExplosions(dt);
    updateSpawners(dt);
    gameOverCheck();
  }

  function drawGrid() {
    ctx.fillStyle = '#1f4b2f';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.strokeStyle = 'rgba(255,255,255,0.12)';
    ctx.lineWidth = 2;

    for (let r = 0; r <= GRID.rows; r++) {
      const y = GRID.top + r * state.grid.cellH;
      ctx.beginPath();
      ctx.moveTo(GRID.left, y);
      ctx.lineTo(canvas.width - GRID.right, y);
      ctx.stroke();
    }

    for (let c = 0; c <= GRID.cols; c++) {
      const x = GRID.left + c * state.grid.cellW;
      ctx.beginPath();
      ctx.moveTo(x, GRID.top);
      ctx.lineTo(x, canvas.height - GRID.bottom);
      ctx.stroke();
    }

    for (let r = 0; r < GRID.rows; r++) {
      for (let c = 0; c < GRID.cols; c++) {
        const plantId = boardGet(r, c);
        if (plantId === null) {
          continue;
        }

        const plant = state.plants.find((candidate) => candidate.id === plantId);
        if (!plant) {
          continue;
        }
        drawPlant(plant);
      }
    }

    state.zombies.forEach((zombie) => drawZombie(zombie));
    state.projectiles.forEach((bullet) => drawBullet(bullet));
    state.suns.forEach((sun) => drawSun(sun));
    state.explosions.forEach((boom) => drawExplosion(boom));

    if (state.paused) {
      ctx.fillStyle = 'rgba(0,0,0,0.42)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#fff';
      ctx.font = '48px Trebuchet MS';
      ctx.textAlign = 'center';
      ctx.fillText('Paused', canvas.width / 2, canvas.height / 2);
      ctx.textAlign = 'left';
    }

    const path = ['Lanes: 5', 'Rows'];
    ctx.fillStyle = '#9dd0ff';
    ctx.font = '16px Trebuchet MS';
    ctx.fillText(path[0], 8, GRID.top - 12);
    ctx.fillText(path[1], 52, GRID.top - 12);
  }

  function drawPlant(plant) {
    const data = PLANT_TYPES[plant.type];
    if (!data) {
      return;
    }

    const sizeW = state.grid.cellW * 0.64;
    const sizeH = state.grid.cellH * 0.8;
    const x = plant.x - sizeW / 2;
    const y = plant.y - sizeH / 2;

    ctx.fillStyle = data.color;
    ctx.fillRect(x, y, sizeW, sizeH);

    ctx.fillStyle = '#142a15';
    ctx.font = '12px Trebuchet MS';
    ctx.textAlign = 'center';
    ctx.fillText(data.name.slice(0, 2), plant.x, y + sizeH - 6);

    const barW = sizeW * 0.9;
    const barH = 5;
    const healthRatio = Math.max(0, plant.health / data.maxHealth);
    ctx.fillStyle = '#420';
    ctx.fillRect(x + (sizeW - barW) / 2, y + 3, barW, barH);
    ctx.fillStyle = '#af0';
    ctx.fillRect(x + (sizeW - barW) / 2, y + 3, barW * healthRatio, barH);

    if (plant.type === 'sun') {
      ctx.fillStyle = '#ffea74';
      ctx.beginPath();
      ctx.arc(plant.x - sizeW * 0.12, y + sizeH * 0.28, 4, 0, Math.PI * 2);
      ctx.fill();
    }

    if (plant.type === 'potato' && plant.armed) {
      ctx.fillStyle = '#ffd9f0';
      ctx.fillText('ARM', plant.x, y + 13);
    }

    if (plant.type === 'potato' && !plant.armed) {
      const fraction = Math.min(1, (1.5 - plant.armDelay) / 1.5);
      const barRadius = sizeW * 0.42;
      ctx.strokeStyle = '#e0b8ff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(plant.x, y + 7, barRadius, Math.PI * 1.2, Math.PI * 1.2 + Math.PI * 1.7 * fraction);
      ctx.stroke();
    }

    if (plant.type === 'cherry') {
      const ratio = 1 - Math.max(0, plant.delay / 1.5);
      ctx.fillStyle = '#fffbf2';
      ctx.fillRect(x + 3, y + 4, sizeW * ratio, 3);
    }
  }

  function drawZombie(zombie) {
    ctx.fillStyle = zombie.color || '#8de7ff';
    ctx.fillRect(zombie.x, zombie.y, zombie.w, zombie.h);

    const eyeY = zombie.y + 10;
    ctx.fillStyle = '#0b2036';
    ctx.fillRect(zombie.x + 8, eyeY, 6, 6);

    const hpRatio = Math.max(0, zombie.health / zombie.maxHealth);
    const barW = zombie.w * 0.9;
    const barX = zombie.x + zombie.w * 0.05;
    const barY = zombie.y - 8;

    ctx.fillStyle = '#5a2a2a';
    ctx.fillRect(barX, barY, barW, 4);
    ctx.fillStyle = '#f77';
    ctx.fillRect(barX, barY, barW * hpRatio, 4);
  }

  function drawBullet(bullet) {
    ctx.fillStyle = '#9aff91';
    ctx.beginPath();
    ctx.arc(bullet.x, bullet.y, bullet.radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#d4ffd8';
    ctx.beginPath();
    ctx.arc(bullet.x, bullet.y, bullet.radius * 0.45, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawSun(sun) {
    const pulse = sun.fromPlant ? 0.92 : 1;
    ctx.fillStyle = '#ffdc4f';
    ctx.beginPath();
    ctx.arc(sun.x, sun.y, sun.radius * pulse, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#7e5d00';
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = '#6a4500';
    ctx.font = '10px Trebuchet MS';
    ctx.textAlign = 'center';
    ctx.fillText(`+${sun.value}`, sun.x, sun.y + 3);
    ctx.textAlign = 'left';
  }

  function drawExplosion(boom) {
    const progress = 1 - boom.life / boom.maxLife;
    const radius = boom.radius * (1 + progress);
    const alpha = 0.72 * (1 - progress);
    const gradient = ctx.createRadialGradient(boom.x, boom.y, 0, boom.x, boom.y, radius);
    gradient.addColorStop(0, `rgba(255, 200, 40, ${alpha})`);
    gradient.addColorStop(1, `rgba(255, 70, 20, 0)`);

    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(boom.x, boom.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  function render() {
    drawGrid();

    if (state.gameOver) {
      ctx.fillStyle = 'rgba(0,0,0,0.45)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#ffefb5';
      ctx.font = '54px Trebuchet MS';
      ctx.textAlign = 'center';
      ctx.fillText('GAME OVER', canvas.width / 2, canvas.height / 2 - 18);
      ctx.font = '26px Trebuchet MS';
      ctx.fillText(`Score: ${state.score}`, canvas.width / 2, canvas.height / 2 + 18);
      ctx.textAlign = 'left';
    }

    updateHUD();
  }

  function addPlant(row, col, type) {
    if (type !== 'pea' && type !== 'sun' && type !== 'wall' && type !== 'cherry' && type !== 'potato') {
      return;
    }

    const data = PLANT_TYPES[type];
    if (!data) {
      return;
    }

    const cost = data.cost;
    if (state.sun < cost) {
      setStatus(`Not enough sun for ${data.name}. Need ${cost}.`);
      return;
    }

    const occupied = boardGet(row, col);
    if (occupied !== null) {
      setStatus('That cell is occupied.');
      return;
    }

    const world = cellToWorld(row, col);
    const plant = {
      id: state.nextPlantId++,
      type,
      row,
      col,
      x: world.x,
      y: world.y,
      health: data.maxHealth,
      shootCooldown: 1.2,
      sunCooldown: 9,
      delay: 1.5,
      armDelay: 1.4,
      armed: false,
      exploding: false,
    };

    boardSet(row, col, plant.id);
    state.plants.push(plant);
    state.sun -= cost;
    setStatus(`Placed ${data.name}.`);
  }

  function onCanvasClick(event) {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * (canvas.width / rect.width);
    const y = (event.clientY - rect.top) * (canvas.height / rect.height);

    for (let i = state.suns.length - 1; i >= 0; i--) {
      const sun = state.suns[i];
      const dx = sun.x - x;
      const dy = sun.y - y;
      if (dx * dx + dy * dy <= sun.radius * sun.radius) {
        state.sun += sun.value;
        state.score += 1;
        state.suns.splice(i, 1);
        setStatus('Collected 25 sun.');
        return;
      }
    }

    const cell = worldToCell(x, y);
    if (!cell) {
      return;
    }

    if (state.selectedPlant === 'shovel') {
      const plantId = boardGet(cell.row, cell.col);
      if (plantId === null) {
        setStatus('No plant there to remove.');
        return;
      }
      const idx = state.plants.findIndex((p) => p.id === plantId);
      if (idx >= 0) {
        removePlantAtIndex(idx);
        setStatus('Plant removed.');
      }
      return;
    }

    addPlant(cell.row, cell.col, state.selectedPlant);
  }

  function bindSelectors() {
    const cards = document.querySelectorAll('.plant-card');
    cards.forEach((card) => {
      card.addEventListener('click', () => {
        const type = card.dataset.plant;
        if (!PLANT_TYPES[type]) {
          return;
        }
        state.selectedPlant = type;
        cards.forEach((item) => item.classList.remove('selected'));
        card.classList.add('selected');
        setStatus(`Selected ${PLANT_TYPES[type].name}.`);
        if (shovelBtn) {
          shovelBtn.classList.remove('selected');
        }
      });
    });

    if (shovelBtn) {
      shovelBtn.addEventListener('click', () => {
        const cards = document.querySelectorAll('.plant-card');
        cards.forEach((card) => card.classList.remove('selected'));
        state.selectedPlant = 'shovel';
        setStatus('Selected Shovel. Click a cell to remove a plant.');
        shovelBtn.classList.add('selected');
      });
    }

    if (pauseBtn) {
      pauseBtn.addEventListener('click', () => {
        if (state.gameOver) {
          return;
        }

        state.paused = !state.paused;
        state.running = !state.paused;
        pauseBtn.dataset.state = state.paused ? 'paused' : 'running';
        pauseBtn.textContent = state.paused ? 'Resume' : 'Pause';
        setStatus(state.paused ? 'Paused.' : 'Resumed.');
        if (!state.paused) {
          lastTime = performance.now();
        }
      });
    }

    if (restartBtn) {
      restartBtn.addEventListener('click', () => {
        initState();
        setStatus('Restarted. New game started.');
      });
    }
  }

  function gameLoop(timestamp) {
    const dt = Math.min(0.04, (timestamp - lastTime) / 1000);
    lastTime = timestamp;

    update(dt);
    render();
    requestAnimationFrame(gameLoop);
  }

  canvas.addEventListener('click', onCanvasClick);
  bindSelectors();
  window.addEventListener('resize', computeGridMetrics);

  initState();
})();
