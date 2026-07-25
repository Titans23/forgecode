export const GRID_ROWS = 5;
export const GRID_COLS = 9;
export const CELL_W = 80;
export const CELL_H = 100;

export const BOARD_X = 20;
export const BOARD_Y = 120;

export const CANVAS_W = 1040;
export const CANVAS_H = 690;

export const DEFAULT_SUN = 150;
export const START_LIVES = 5;

export const PLANT_TYPES = {
  peashooter: {
    id: 'peashooter',
    name: '豌豆射手',
    cost: 100,
    cooldownMs: 7000,
    hp: 140,
    fireInterval: 0.9,
    damage: 16,
    color: '#3b9950',
    range: Infinity,
    bulletSpeed: 300,
    description: '基础远程攻击植物，持续射击最近僵尸。'
  },
  snowpea: {
    id: 'snowpea',
    name: '寒冰射手',
    cost: 175,
    cooldownMs: 9000,
    hp: 140,
    fireInterval: 1.0,
    damage: 14,
    color: '#4ca7ff',
    bulletSpeed: 280,
    slowFactor: 0.45,
    slowMs: 2500,
    description: '伤害较低，但会使僵尸减速，便于群控。'
  },
  sunflower: {
    id: 'sunflower',
    name: '向日葵',
    cost: 75,
    cooldownMs: 5500,
    hp: 90,
    produceInterval: 6.5,
    produceAmount: 25,
    color: '#ffb200',
    description: '周期性掉落阳光。'
  },
  wallnut: {
    id: 'wallnut',
    name: '坚果墙',
    cost: 125,
    cooldownMs: 6000,
    hp: 420,
    color: '#86592c',
    description: '高血量肉盾，主要用于堵路。'
  }
};

export const ZOMBIE_TYPES = {
  normal: {
    id: 'normal',
    name: '普通僵尸',
    hp: 90,
    speed: 35,
    damage: 14,
    attackInterval: 1.2,
    color: '#7d8a73'
  },
  conehead: {
    id: 'conehead',
    name: '路障僵尸',
    hp: 140,
    speed: 29,
    damage: 16,
    attackInterval: 1.1,
    color: '#8d673f'
  },
  bucket: {
    id: 'bucket',
    idFriendly: '油桶僵尸',
    hp: 260,
    speed: 24,
    damage: 20,
    attackInterval: 1.0,
    color: '#4b5f76'
  }
};

export const SKY_SUN_INTERVAL_MS = 12000;

export const WAVES = {
  baseCount: 6,
  spawnIntervalMs: 2200,
  bonusPerWave: 2,
  extraIntervalMin: 900,
  speedScale: 0.6
};
