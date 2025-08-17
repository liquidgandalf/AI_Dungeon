# SkeletonGame/app/game.py
import random
import copy
import time
import math
import pygame
from typing import Dict, Tuple, List, Any
from app.server import players, socketio
from app import config as game_config
from app.items import ITEM_DB, get_weight, backpack_capacity
from app import enemy_ai

# Screen and board
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720
SIDEBAR_WIDTH = 240
BORDER = 4
GAME_X = SIDEBAR_WIDTH + BORDER
GAME_W = SCREEN_WIDTH - GAME_X - BORDER
GAME_H = SCREEN_HEIGHT - 2 * BORDER

# Tile/grid config (centered board)
TILE_SIZE = 4
GRID_W = 256
GRID_H = 128
BOARD_PX_W = GRID_W * TILE_SIZE  # 1024
BOARD_PX_H = GRID_H * TILE_SIZE  # 512
BOARD_ORIGIN_X = GAME_X + (GAME_W - BOARD_PX_W) // 2
BOARD_ORIGIN_Y = BORDER + (GAME_H - BOARD_PX_H) // 2

# Player tile step (one tile per command)
PLAYER_SIZE = TILE_SIZE
STEP = TILE_SIZE

# Local runtime state maintained by the game loop
player_state: Dict[str, Dict[str, Tuple[int, int]]] = {}

# Tile types
EMPTY = 0
WALL = 1
# Special tile type id for biome spawners (entity metadata; grid remains EMPTY)
SPAWNER_TILE = 2

# Grid and occupancy
grid = None  # will be a list[list[int]] sized GRID_H x GRID_W
occupied: Dict[Tuple[int, int], str] = {}
## Per-wall tile hitpoints; 0 for non-walls
wall_hp: List[List[int]] = []
WALL_HP_BASE: int = 3
WALL_HP_PER_BIOME: int = 1

# World entities loaded from config (items, enemies)
world_entities: List[Dict[str, Any]] = []
entities_inited = False
# Cells blocked by solid entities (e.g., items/props/spawners)
solid_cells: set = set()

# Enemy instances and occupancy
enemies: Dict[str, Dict[str, Any]] = {}
random_enemies_inited: bool = False

def enemy_occupied_cells() -> Dict[Tuple[int,int], str]:
    occ: Dict[Tuple[int,int], str] = {}
    for eid, e in enemies.items():
        pos = e.get('pos')
        if not pos:
            continue
        cx, cy = int(pos[0]), int(pos[1])
        occ[(cx, cy)] = eid
    return occ

# Cache of enemy type definitions by type id for rendering pings
_ENEMY_TYPE_MAP: Dict[str, Dict[str, Any]] = {}

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))

def make_enemy_instance(etype: str, cx: int, cy: int, spawner_id: str = None) -> Dict[str, Any]:
    """Materialize an enemy instance from its type template at cell (cx, cy).
    Copies all attributes so per-instance mutation does not affect the template.
    Ensures required stats exist and clamps speed to 0..256.
    """
    types = get_enemy_type_map()
    tdef = dict(types.get(str(etype), {}))
    stats_base = copy.deepcopy(tdef.get('stats') or {})
    # Ensure required stats with defaults
    for k, dv in (
        ('health', 1),
        ('speed', 0),
        ('damaged', 0),
        ('durability', 0),
        ('stamina', 0),
        ('attack', 0),
        ('defense', 0),
    ):
        if k not in stats_base:
            stats_base[k] = dv
    stats_base['speed'] = clamp(stats_base.get('speed', 0), 0, 256)
    stats_current = copy.deepcopy(stats_base)
    # Determine biome at spawn
    try:
        b = int(biomes[cy][cx]) if (0 <= cy < len(biomes) and 0 <= cx < len(biomes[0])) else 0
    except Exception:
        b = 0
    # Build instance
    eid = f"e{len(enemies) + 1}"
    inst: Dict[str, Any] = {
        'id': eid,
        'type': etype,
        'name': tdef.get('name'),
        'image': tdef.get('image'),
        'pingcolour': tdef.get('pingcolour', [80, 200, 120]),
        'pos': [float(cx) + 0.5, float(cy) + 0.5],
        'biome': b,
        'spawner': spawner_id,
        'stats_base': stats_base,
        'stats_current': stats_current,
        'hp': int(stats_current.get('health', 1)),
        'speed': int(stats_current.get('speed', 0)),
        'next_move_ts': 0.0,
        'dir': [0, 0],  # last chosen move direction (dx, dy)
    }
    # Initialize timer with slight jitter so not all enemies move together
    interval = None
    try:
        interval = None if inst['speed'] <= 0 else (3.0 - 2.0 * ((clamp(inst['speed'], 1, 256) - 1) / (256 - 1)))
    except Exception:
        interval = None
    if interval is not None and interval > 0:
        now = time.time()
        inst['next_move_ts'] = now + random.uniform(0.0, interval)
    # Initialize a random direction (not 0,0)
    dirs = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if not (dx == 0 and dy == 0)]
    rdx, rdy = random.choice(dirs)
    inst['dir'] = [int(rdx), int(rdy)]
    return inst

def get_enemy_type_map() -> Dict[str, Dict[str, Any]]:
    global _ENEMY_TYPE_MAP
    if not _ENEMY_TYPE_MAP:
        try:
            types = game_config.get_enemy_types()
            _ENEMY_TYPE_MAP = {t.get('type'): t for t in types if t.get('type')}
        except Exception:
            _ENEMY_TYPE_MAP = {}
    return _ENEMY_TYPE_MAP


def render_enemy_pings(screen: pygame.surface.Surface, visible: List[List[bool]] = None):
    """Draw pulsing radar pings at enemy positions using their type pingcolour.
    On by default; later can be gated by configs or player items.
    """
    if not enemies:
        return
    types = get_enemy_type_map()
    t = time.time()
    # pulse radius in pixels
    r = 4 + int((math.sin(t * 2.0) + 1.0) * 0.5 * 12)  # 4..16
    size = r * 2 + 4
    # Pre-make surface per distinct colour to reduce overdraw setup
    surf_cache: Dict[Tuple[int,int,int,int], pygame.Surface] = {}

    for e in enemies.values():
        etype = str(e.get('type', ''))
        info = types.get(etype) or {}
        col = info.get('pingcolour', [255, 0, 255])  # magenta default
        color_a = (int(col[0]), int(col[1]), int(col[2]), 140)
        surf = surf_cache.get(color_a)
        if surf is None:
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.circle(surf, color_a, (r+2, r+2), r, width=2)
            surf_cache[color_a] = surf
        pos = e.get('pos')
        if not pos:
            continue
        cx, cy = int(pos[0]), int(pos[1])
        if visible is not None:
            if not (0 <= cy < len(visible) and 0 <= cx < len(visible[0]) and visible[cy][cx]):
                continue
        px, py = cell_to_px(cx, cy)
        # center over tile
        screen.blit(surf, (px + TILE_SIZE//2 - (r+2), py + TILE_SIZE//2 - (r+2)))


def init_random_enemies_once():
    """Spawn a configurable number of random enemies scattered across the map.
    Uses game_config.spawns.random_enemies. Places only on EMPTY tiles, not in
    solid item cells, and avoids overlapping other enemies.
    """
    global random_enemies_inited
    if random_enemies_inited:
        return
    try:
        cfg = game_config.get_game_config()
        count = int(((cfg or {}).get('spawns') or {}).get('random_enemies', 0))
    except Exception:
        count = 0
    if count <= 0:
        return
    types = game_config.get_enemy_types()
    type_ids = [t.get('type') for t in types if t.get('type')]
    if not type_ids:
        return
    occ = set(enemy_occupied_cells().keys())

    spawned = 0
    attempts = 0
    max_attempts = max(200, count * 50)
    while spawned < count and attempts < max_attempts:
        attempts += 1
        x = random.randint(1, GRID_W - 2)
        y = random.randint(1, GRID_H - 2)
        if grid[y][x] != EMPTY:
            continue
        if (x, y) in solid_cells:
            continue
        if (x, y) in occ:
            continue
        etype = random.choice(type_ids)
        inst = make_enemy_instance(etype, x, y, spawner_id=None)
        enemies[inst['id']] = inst
        occ.add((x, y))
        spawned += 1
    random_enemies_inited = True


def render_enemies(screen: pygame.surface.Surface, visible: List[List[bool]] = None):
    """Render enemies as simple colored circles using their pingcolour.
    This is a placeholder until sprite-based rendering is added.
    """
    if not enemies:
        return
    types = get_enemy_type_map()
    for e in enemies.values():
        pos = e.get('pos')
        if not pos:
            continue
        cx, cy = int(pos[0]), int(pos[1])
        if visible is not None:
            if not (0 <= cy < len(visible) and 0 <= cx < len(visible[0]) and visible[cy][cx]):
                continue
        px, py = cell_to_px(cx, cy)
        info = types.get(str(e.get('type', ''))) or {}
        col = e.get('pingcolour', info.get('pingcolour', [80, 200, 120]))
        color = (int(col[0]), int(col[1]), int(col[2]))
        # Draw filled circle with a darker outline
        center = (px + TILE_SIZE//2, py + TILE_SIZE//2)
        radius = max(6, TILE_SIZE//3)
        pygame.draw.circle(screen, color, center, radius)
        outline = tuple(max(0, c - 60) for c in color)
        pygame.draw.circle(screen, outline, center, radius, width=2)

# Biomes grid parallel to 'grid' holding biome id per tile: 0..6
biomes: List[List[int]] = []
# Biome centers and radius used for rendering overlaps
biome_centers: List[Tuple[int,int,int]] = []  # (cx, cy, biome_id)
biome_radius: int = 0

# Simple biome -> sky RGB palette (0..6), aligned with board biome_colors
BIOME_SKY_COLORS: Dict[int, Tuple[int,int,int]] = {
    0: (0, 0, 0),         # no biome -> black sky
    1: (255, 120, 120),   # red
    2: (255, 190, 120),   # orange
    3: (255, 255, 150),   # yellow
    4: (120, 220, 150),   # green
    5: (150, 200, 255),   # blue
    6: (200, 150, 255),   # purple
}

def biome_sky_colour_at(cx: int, cy: int) -> Tuple[int,int,int]:
    try:
        bid = int(biomes[cy][cx]) if (0 <= cy < len(biomes) and 0 <= cx < len(biomes[0])) else 0
    except Exception:
        bid = 0
    return BIOME_SKY_COLORS.get(bid, BIOME_SKY_COLORS[0])


def init_grid_once():
    global grid, biomes, wall_hp, WALL_HP_BASE, WALL_HP_PER_BIOME
    if grid is not None:
        return
    # Start all walls
    g = [[WALL for _ in range(GRID_W)] for _ in range(GRID_H)]
    # Generate maze into g
    generate_maze(g, corridor_w=3, wall_w=1, room_prob=0.08)
    grid = g
    # After maze, generate biomes
    biomes = generate_biomes()
    # Configure wall HP scaling from config (optional)
    try:
        walls_cfg = (game_config.get_game_config() or {}).get('walls') or {}
        WALL_HP_BASE = int(walls_cfg.get('hp_base', 3))
        WALL_HP_PER_BIOME = int(walls_cfg.get('hp_per_biome', 1))
    except Exception:
        WALL_HP_BASE, WALL_HP_PER_BIOME = 3, 1
    # Helper to compute local max hp based on biome id
    def _hp_max_at(x: int, y: int) -> int:
        try:
            bid = int(biomes[y][x])
        except Exception:
            bid = 0
        return max(1, int(WALL_HP_BASE + WALL_HP_PER_BIOME * bid))
    # Initialize wall hp grid now that biomes exist
    wall_hp = [[((_hp_max_at(x, y)) if grid[y][x] == WALL else 0) for x in range(GRID_W)] for y in range(GRID_H)]

def generate_biomes() -> List[List[int]]:
    """Create biome ids per tile (0..6). 0 = default. 1..6 = colored biomes.
    Places N centers and fills circular regions of configurable radius.
    """
    try:
        cfg = game_config.get_game_config()
        bio_cfg = cfg.get('biomes', {}) or {}
        count = int(bio_cfg.get('count', 6))
        radius = int(bio_cfg.get('radius', 24))  # in tiles
    except Exception:
        count, radius = 6, 24
    b = [[0 for _ in range(GRID_W)] for _ in range(GRID_H)]
    centers: List[Tuple[int,int,int]] = []  # (x,y,id)
    # Big room radius (in tiles) carved at each biome center
    room_r = 12
    room_r2 = room_r * room_r
    # Distribute centers by partitioning the map into segments ~aspect aligned
    # Determine cols/rows close to world aspect for the given count
    if count <= 0:
        return b
    aspect = GRID_W / max(1, GRID_H)
    cols = max(1, int(round((count * aspect) ** 0.5)))
    rows = max(1, (count + cols - 1) // cols)
    # If too many cells, trim later
    seg_w = GRID_W / cols
    seg_h = GRID_H / rows
    margin = 2  # keep away from hard borders a bit
    # biome centers must be >= max(5, room_r) tiles from outer edges so rooms always fit
    min_edge = max(5, room_r)
    # Shuffle biome ids so their numbering/colors are randomized across segments each run
    shuffled_ids = list(range(1, count + 1))
    random.shuffle(shuffled_ids)
    idx = 1
    for r in range(rows):
        for c in range(cols):
            if idx > count:
                break
            x0 = int(c * seg_w)
            y0 = int(r * seg_h)
            x1 = int((c + 1) * seg_w) - 1
            y1 = int((r + 1) * seg_h) - 1
            # clamp within interior (avoid outer border) and enforce min edge distance
            x0 = max(1 + min_edge, x0 + margin)
            y0 = max(1 + min_edge, y0 + margin)
            x1 = min(GRID_W - 2 - min_edge, x1 - margin)
            y1 = min(GRID_H - 2 - min_edge, y1 - margin)
            if x1 < x0 or y1 < y0:
                # fallback to interior respecting min_edge
                cx = random.randrange(1 + min_edge, GRID_W - 1 - min_edge)
                cy = random.randrange(1 + min_edge, GRID_H - 1 - min_edge)
            else:
                cx = random.randrange(x0, x1 + 1)
                cy = random.randrange(y0, y1 + 1)
            centers.append((cx, cy, shuffled_ids[idx - 1]))
            idx += 1
    # Carve large rooms at biome centers if entirely within interior
    for (cx, cy, _bid) in centers:
        if cx - room_r < 1 or cx + room_r > GRID_W - 2 or cy - room_r < 1 or cy + room_r > GRID_H - 2:
            continue  # would cross outer wall; skip
        for y in range(cy - room_r, cy + room_r + 1):
            row = grid[y]
            dy = y - cy
            for x in range(cx - room_r, cx + room_r + 1):
                dx = x - cx
                if dx*dx + dy*dy <= room_r2:
                    row[x] = EMPTY

    # Persist centers and radius for rendering time blending
    global biome_centers, biome_radius
    biome_centers = centers
    biome_radius = radius
    r2 = radius * radius
    for y in range(1, GRID_H - 1):
        for x in range(1, GRID_W - 1):
            # assign the first circle that contains this tile; could also pick nearest
            for (cx, cy, bid) in centers:
                dx = x - cx
                dy = y - cy
                if dx*dx + dy*dy <= r2:
                    b[y][x] = bid
                    break
    return b


def init_entities_once():
    global entities_inited, world_entities
    if entities_inited:
        return
    ents = []
    for e in game_config.get_map_entities():
        et = dict(e)
        et.setdefault('state', 'idle')
        et.setdefault('anim_t', 0.0)  # seconds accumulator
        ents.append(et)
    # Random item spawns
    try:
        cfg = game_config.get_game_config()
        spawn_n = int((cfg.get('spawns') or {}).get('random_items', 0))
    except Exception:
        spawn_n = 0
    if spawn_n > 0:
        ents.extend(item_generator(spawn_n))
    # Random chest spawns (always visible props)
    try:
        cfg = game_config.get_game_config()
        chest_n = int((cfg.get('spawns') or {}).get('random_chests', 0))
    except Exception:
        chest_n = 0
    if chest_n > 0:
        ents.extend(chest_generator(chest_n))
    # Spawn one demon spawner at each biome center (treat as item entity)
    for (cx, cy, bid) in (biome_centers or []):
        # Avoid conflicting with another entity at same integer cell
        conflict = False
        for e in ents:
            pos = e.get('pos')
            if not pos:
                continue
            ex, ey = int(pos[0]), int(pos[1])
            if ex == cx and ey == cy:
                conflict = True
                break
        if conflict:
            continue
        ents.append({
            'type': 'item',
            'item_id': 'demon_spawn',
            'pos': [float(cx) + 0.5, float(cy) + 0.5],
            'tile_type': SPAWNER_TILE,
            'biome_id': bid,
            'sprite': {
                'image': 'items/demonspawn.png',
                'base_width': 64,
                'base_height': 64,
                'scale': 1.0,
                'y_offset': 0,
            }
        })

    world_entities = ents
    entities_inited = True
    rebuild_solid_cells()


def rebuild_solid_cells():
    """Recompute the set of grid cells blocked by solid world entities."""
    global solid_cells
    s = set()
    for ent in world_entities:
        # Treat items (including chests and spawners) as solid for movement
        if (ent.get('type') or 'item') != 'item':
            continue
        pos = ent.get('pos') or ent.get('position')
        if not pos or len(pos) < 2:
            continue
        ex, ey = int(float(pos[0])), int(float(pos[1]))
        if 0 <= ex < GRID_W and 0 <= ey < GRID_H:
            s.add((ex, ey))
    solid_cells = s


def backpack_capacity_for_player(pdata: Dict[str, Any]) -> float:
    eq = pdata.get('equipment') or {}
    bp_id = eq.get('backpack')
    return backpack_capacity(bp_id) if bp_id else 0.0


def try_add_to_backpack(pdata: Dict[str, Any], item_id: str) -> bool:
    cap = backpack_capacity_for_player(pdata)
    if cap <= 0:
        return False
    used = float(pdata.get('backpack_weight_used', 0.0))
    w = float(get_weight(item_id))
    if used + w <= cap + 1e-6:
        inv = pdata.setdefault('inventory', [])
        inv.append(item_id)
        pdata['backpack_weight_used'] = used + w
        return True
    return False


def drop_item_near(cx: int, cy: int, item_id: str):
    """Drop an item world entity on a nearby empty tile if possible."""
    # 4-neighborhood preference
    for dx, dy in ((1,0), (-1,0), (0,1), (0,-1)):
        nx, ny = cx + dx, cy + dy
        if not (0 <= nx < GRID_W and 0 <= ny < GRID_H):
            continue
        if grid[ny][nx] != EMPTY:
            continue
        if (nx, ny) in occupied or (nx, ny) in solid_cells:
            continue
        # Ensure no other entity at that integer cell
        conflict = False
        for ent in world_entities:
            pos = ent.get('pos') or ent.get('position')
            if not pos:
                continue
            ex, ey = int(float(pos[0])), int(float(pos[1]))
            if ex == nx and ey == ny:
                conflict = True
                break
        if conflict:
            continue
        world_entities.append({
            'type': 'item',
            'item_id': item_id,
            'pos': [float(nx) + 0.5, float(ny) + 0.5],
            'sprite': {
                'image': f'items/{item_id}.png',
                'base_width': 64,
                'base_height': 64,
                'scale': 1.0,
                'y_offset': 0,
            }
        })
        rebuild_solid_cells()
        return True
    return False


def item_generator(count: int) -> List[Dict[str, Any]]:
    """Generate 'count' random pickup items placed at empty grid cells.
    Uses ITEM_DB ids and assumes a default image at static/img/items/<id>.png.
    Filters out container-like entries with no allowed slots (e.g., chests).
    """
    items: List[Dict[str, Any]] = []
    # Candidate item ids: those with allowed_slots
    candidates = [
        it_id for it_id, it in ITEM_DB.items()
        if it and it.get('allowed_slots') and bool(it.get('active', True))
    ]
    if not candidates:
        return items
    for _ in range(max(0, count)):
        cx, cy = random_empty_cell()
        pos = [float(cx) + 0.5, float(cy) + 0.5]
        item_id = random.choice(candidates)
        # Default sprite guesses 64x64 image named after item id
        items.append({
            'type': 'item',
            'item_id': item_id,
            'pos': pos,
            'sprite': {
                'image': f'items/{item_id}.png',
                'base_width': 64,
                'base_height': 64,
                'scale': 1.0,
                'y_offset': 0,
            }
        })
    return items


def chest_generator(count: int, item_id: str = 'chest_basic', image: str = 'items/chest.png') -> List[Dict[str, Any]]:
    """Spawn decorative/interactive chest items, regardless of allowed_slots.
    They render as billboards using the provided image.
    """
    out: List[Dict[str, Any]] = []
    for _ in range(max(0, count)):
        cx, cy = random_empty_cell()
        pos = [float(cx) + 0.5, float(cy) + 0.5]
        out.append({
            'type': 'item',
            'item_id': item_id,
            'pos': pos,
            'sprite': {
                'image': image,
                'base_width': 64,
                'base_height': 64,
                'scale': 1.0,
                'y_offset': 0,
            }
        })
    return out


def carve_rect(g, x0, y0, x1, y1, val=EMPTY):
    # inclusive rect bounds
    for y in range(max(0, y0), min(GRID_H, y1 + 1)):
        row = g[y]
        for x in range(max(0, x0), min(GRID_W, x1 + 1)):
            row[x] = val


def generate_maze(g, corridor_w=3, wall_w=1, room_prob=0.08):
    """Carve a maze with 3-wide corridors separated by 1-wide walls,
    centered within existing board area (we already leave a 1-tile outer wall).
    Uses a randomized DFS on a macro-grid where each macro-cell expands
    to a corridor_w x corridor_w block. Openings between cells are corridor_w wide.
    Also occasionally expands cells into simple rooms by merging neighbors.
    """
    cw = corridor_w
    ww = wall_w
    stride = cw + ww  # 4

    # Ensure perimeter stays walls
    # Interior macro grid spans indices so that macro cell i,j maps to block
    # base at (1 + i*stride, 1 + j*stride)
    MW = max(1, (GRID_W - 1) // stride)
    MH = max(1, (GRID_H - 1) // stride)

    def cell_base(i, j):
        bx = 1 + i * stride
        by = 1 + j * stride
        return bx, by

    def carve_cell(i, j):
        bx, by = cell_base(i, j)
        carve_rect(g, bx, by, bx + cw - 1, by + cw - 1, EMPTY)

    def open_between(i1, j1, i2, j2):
        # Open the separating 1-tile wall fully across corridor width
        bx1, by1 = cell_base(i1, j1)
        bx2, by2 = cell_base(i2, j2)
        if i2 == i1 + 1 and j2 == j1:  # open vertical wall to the right
            wx = bx1 + cw  # wall column between
            carve_rect(g, wx, by1, wx, by1 + cw - 1, EMPTY)
        elif i2 == i1 - 1 and j2 == j1:  # to the left
            wx = bx2 + cw
            carve_rect(g, wx, by2, wx, by2 + cw - 1, EMPTY)
        elif j2 == j1 + 1 and i2 == i1:  # down
            wy = by1 + cw  # wall row between
            carve_rect(g, bx1, wy, bx1 + cw - 1, wy, EMPTY)
        elif j2 == j1 - 1 and i2 == i1:  # up
            wy = by2 + cw
            carve_rect(g, bx2, wy, bx2 + cw - 1, wy, EMPTY)

    visited = [[False for _ in range(MW)] for _ in range(MH)]

    # Random starting cell
    stack = [(random.randrange(MW), random.randrange(MH))]
    visited[stack[0][1]][stack[0][0]] = True
    carve_cell(stack[0][0], stack[0][1])

    def neighbors(i, j):
        opts = []
        if i > 0:
            opts.append((i - 1, j))
        if i + 1 < MW:
            opts.append((i + 1, j))
        if j > 0:
            opts.append((i, j - 1))
        if j + 1 < MH:
            opts.append((i, j + 1))
        random.shuffle(opts)
        return opts

    while stack:
        i, j = stack[-1]
        # Occasionally expand to a room by carving a 2x2 or 3x2 macro area
        if random.random() < room_prob:
            rw = random.choice((2, 3)) if i + 2 < MW else 2
            rh = random.choice((1, 2)) if j + 2 < MH else 1
            # carve union area and mark visited
            for dj in range(rh):
                for di in range(rw):
                    ii, jj = i + di, j + dj
                    if 0 <= ii < MW and 0 <= jj < MH and not visited[jj][ii]:
                        open_between(i, j, ii, jj)
                        carve_cell(ii, jj)
                        visited[jj][ii] = True
                        stack.append((ii, jj))
            # continue DFS from the latest addition
            continue

        # Normal DFS step
        unv = [(ii, jj) for (ii, jj) in neighbors(i, j) if not visited[jj][ii]]
        if not unv:
            stack.pop()
            continue
        ni, nj = random.choice(unv)
        open_between(i, j, ni, nj)
        carve_cell(ni, nj)
        visited[nj][ni] = True
        stack.append((ni, nj))


def cell_to_px(cx: int, cy: int) -> Tuple[int, int]:
    return (
        BOARD_ORIGIN_X + cx * TILE_SIZE,
        BOARD_ORIGIN_Y + cy * TILE_SIZE,
    )


def random_empty_cell() -> Tuple[int, int]:
    # Avoid walls by choosing from interior [1..GRID_W-2], [1..GRID_H-2]
    # Try a bounded number of attempts
    for _ in range(500):
        cx = random.randrange(1, GRID_W - 1)
        cy = random.randrange(1, GRID_H - 1)
        if grid[cy][cx] == EMPTY and (cx, cy) not in occupied and (cx, cy) not in solid_cells:
            return (cx, cy)
    # Fallback linear scan if random attempts fail
    for cy in range(1, GRID_H - 1):
        for cx in range(1, GRID_W - 1):
            if grid[cy][cx] == EMPTY and (cx, cy) not in occupied and (cx, cy) not in solid_cells:
                return (cx, cy)
    # If full, place at a safe default
    return (1, 1)


def place_chest_next_to(cx: int, cy: int):
    """Try to place a chest on an adjacent empty tile to (cx, cy)."""
    # Avoid placing multiple chests per player if called again
    # Check 4-neighborhood
    for dx, dy in ((1,0), (-1,0), (0,1), (0,-1)):
        nx, ny = cx + dx, cy + dy
        if not (0 <= nx < GRID_W and 0 <= ny < GRID_H):
            continue
        if grid[ny][nx] != EMPTY:
            continue
        if (nx, ny) in occupied or (nx, ny) in solid_cells:
            continue
        # Avoid overlapping existing entity at same cell (integer check)
        conflict = False
        for ent in world_entities:
            pos = ent.get('pos') or ent.get('position')
            if not pos or len(pos) < 2:
                continue
            ex, ey = int(pos[0]), int(pos[1])
            if ex == nx and ey == ny:
                conflict = True
                break
        if conflict:
            continue
        # Place chest entity
        world_entities.append({
            'type': 'item',
            'item_id': 'chest_basic',
            'pos': [float(nx) + 0.5, float(ny) + 0.5],
            'sprite': {
                'image': 'items/chest.png',
                'base_width': 64,
                'base_height': 64,
                'scale': 1.0,
                'y_offset': 0,
            }
        })
        rebuild_solid_cells()
        return


def tick_enemies():
    """Basic timed random movement per enemy based on speed stat.
    speed 0 => no movement. speed 1 => ~3s per move. speed 256 => ~1s per move.
    """
    if not enemies:
        return
    # Respect config toggle
    try:
        move_enabled = bool(((game_config.get_game_config() or {}).get('enemies') or {}).get('move', True))
    except Exception:
        move_enabled = True
    if not move_enabled:
        return
    now = time.time()
    # Precompute occupancy to avoid overlaps
    e_occ = enemy_occupied_cells()
    for eid, ent in enemies.items():
        try:
            speed = int(ent.get('speed', 0))
        except Exception:
            speed = 0
        if speed <= 0:
            continue
        # Interval mapping: linear from 3.0s at 1 to 1.0s at 256
        s = clamp(speed, 1, 256)
        interval = 3.0 - 2.0 * ((s - 1) / (256 - 1))
        nxt = float(ent.get('next_move_ts') or 0.0)
        if now < nxt:
            continue
        pos = ent.get('pos')
        if not pos or len(pos) < 2:
            continue
        cx, cy = int(pos[0]), int(pos[1])
        # Helper to test passability
        def passable(tx: int, ty: int) -> bool:
            if not (0 <= tx < GRID_W and 0 <= ty < GRID_H):
                return False
            if grid[ty][tx] != EMPTY:
                return False
            if (tx, ty) in occupied:
                return False
            if (tx, ty) in solid_cells:
                return False
            if (tx, ty) in e_occ:
                return False
            return True

        # First try continuing in the current direction
        dx, dy = 0, 0
        d = ent.get('dir')
        if isinstance(d, (list, tuple)) and len(d) == 2:
            try:
                dx, dy = int(d[0]), int(d[1])
            except Exception:
                dx, dy = 0, 0
        nx, ny = cx + dx, cy + dy
        moved = False
        if not (dx == 0 and dy == 0) and passable(nx, ny):
            # Continue moving in same direction
            ent['pos'] = [float(nx) + 0.5, float(ny) + 0.5]
            e_occ.pop((cx, cy), None)
            e_occ[(nx, ny)] = eid
            moved = True
        else:
            # Choose a new random valid direction
            dirs = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if not (dx == 0 and dy == 0)]
            random.shuffle(dirs)
            for ndx, ndy in dirs:
                tx, ty = cx + ndx, cy + ndy
                if passable(tx, ty):
                    ent['dir'] = [ndx, ndy]
                    ent['pos'] = [float(tx) + 0.5, float(ty) + 0.5]
                    e_occ.pop((cx, cy), None)
                    e_occ[(tx, ty)] = eid
                    moved = True
                    break
            # If no valid move, keep direction and stay in place
        # Schedule next move regardless
        ent['next_move_ts'] = now + interval


def ensure_player(sid: str):
    if sid not in player_state:
        init_grid_once()
        # Try to restore from persisted profile if available
        restore_cell = None
        restore_angle = None
        restore_seen = None
        try:
            p = players.get(sid) or {}
            r = p.get('restore') or {}
            rc = r.get('cell')
            ra = r.get('angle')
            rs = r.get('seen')
            if (isinstance(rc, (list, tuple)) and len(rc) == 2
                and isinstance(rc[0], (int, float)) and isinstance(rc[1], (int, float))):
                tx, ty = int(rc[0]), int(rc[1])
                # Validate passability of the restored cell
                if 0 <= tx < GRID_W and 0 <= ty < GRID_H:
                    if grid[ty][tx] == EMPTY and (tx, ty) not in occupied and (tx, ty) not in solid_cells:
                        restore_cell = (tx, ty)
            if isinstance(ra, (int, float)):
                restore_angle = float(ra)
            # Validate seen mask dimensions if provided
            if isinstance(rs, list) and len(rs) == GRID_H and all(isinstance(row, list) and len(row) == GRID_W for row in rs):
                restore_seen = rs
        except Exception:
            restore_cell = None
            restore_angle = None
            restore_seen = None

        if restore_cell is None:
            cx, cy = random_empty_cell()
        else:
            cx, cy = restore_cell
        occupied[(cx, cy)] = sid
        px, py = cell_to_px(cx, cy)
        # Default facing is down (90 deg) unless a restore angle exists
        ang = restore_angle if restore_angle is not None else math.radians(90)
        player_state[sid] = {
            'cell': (cx, cy),
            'pos': (px, py),
            'dir': 'down',  # textual dir not strictly used for movement
            'angle': ang,
            'target_angle': ang,
            'last_frame_ts': 0.0,
            'spawned_chest': False,
        }
        # Initialize per-player seen mask for fog-of-war (or restore persisted)
        try:
            vis_cfg = (game_config.get_game_config() or {}).get('visibility') or {}
            mode = str(vis_cfg.get('mode', 'full'))
            reveal_r = int(vis_cfg.get('reveal_radius', 6))
        except Exception:
            mode, reveal_r = 'full', 6
        # Prefer restored seen if available and valid
        seen = restore_seen if restore_seen is not None else [[False for _ in range(GRID_W)] for _ in range(GRID_H)]
        player_state[sid]['seen'] = seen
        if mode in ('fog', 'reveal'):
            # reveal around spawn
            r2 = reveal_r * reveal_r
            for yy in range(max(0, cy - reveal_r), min(GRID_H, cy + reveal_r + 1)):
                dy = yy - cy
                for xx in range(max(0, cx - reveal_r), min(GRID_W, cx + reveal_r + 1)):
                    dx = xx - cx
                    if dx*dx + dy*dy <= r2:
                        seen[yy][xx] = True
        # Also drop a chest adjacent to the player's spawn for NEW spawns only
        # Do not respawn a chest if we are restoring a returning player
        init_entities_once()
        if restore_cell is None:
            place_chest_next_to(cx, cy)


def apply_command(pos: Tuple[int, int], cmd: str) -> Tuple[int, int]:
    # Here, movement is performed in tile space based on the player's current cell
    # 'pos' is kept for rendering; we'll recompute from cell after movement
    dx = dy = 0
    if cmd == 'up':
        dy = -1
    elif cmd == 'down':
        dy = 1
    elif cmd == 'left':
        dx = -1
    elif cmd == 'right':
        dx = 1

    # Find the player who owns this pos to retrieve its cell
    # This function is called within loop per player; we'll update via outer state
    return pos  # actual movement handled in run loop using cell state


def run_game(screen: pygame.surface.Surface, qr_surface: pygame.surface.Surface):
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 26)
    running = True
    # Raycast render params
    RC_FOV = math.radians(90)
    RC_NUM_RAYS = 400
    RC_W = RC_NUM_RAYS
    RC_H = 160
    RC_MAX_DIST = math.hypot(GRID_W, GRID_H)
    ROT_STEP = math.radians(45)  # target step per left/right command
    ROT_SPEED = math.radians(360)  # deg/sec for smooth rotation

    # Ensure world is initialized before loop
    init_grid_once()
    init_entities_once()
    init_random_enemies_once()

    while running:
        # Events to allow clean quit
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False

        # Background and layout
        screen.fill((0, 0, 0))
        # Sidebar
        pygame.draw.rect(screen, (20, 20, 20), (0, 0, SIDEBAR_WIDTH, SCREEN_HEIGHT))
        # Game border
        pygame.draw.rect(screen, (255, 0, 0), (GAME_X, 0, GAME_W, SCREEN_HEIGHT), BORDER)
        pygame.draw.line(screen, (255, 0, 0), (SIDEBAR_WIDTH, 0), (SIDEBAR_WIDTH, SCREEN_HEIGHT), BORDER)

        # QR code for controller
        screen.blit(qr_surface, (20, 20))
        screen.blit(font.render('Scan to join', True, (200, 200, 200)), (20, 20 + qr_surface.get_height() + 8))

        # Initialize grid and entities once
        init_grid_once()
        init_entities_once()

        # Remove local players that no longer exist on server
        for sid in list(player_state.keys()):
            if sid not in players:
                # free occupied cell
                cell = player_state[sid].get('cell')
                if cell in occupied:
                    occupied.pop(cell, None)
                del player_state[sid]

        # Compute combined visibility mask based on config
        try:
            vis_cfg = (game_config.get_game_config() or {}).get('visibility') or {}
            vis_mode = str(vis_cfg.get('mode', 'full'))
            reveal_r = int(vis_cfg.get('reveal_radius', 6))
        except Exception:
            vis_mode, reveal_r = 'full', 6

        # Update per-player seen masks and build combined mask
        # Treat 'reveal' as alias of 'fog' (persistent reveal on big map)
        if vis_mode in ('fog', 'reveal'):
            # Start all-false combined
            visible_mask = [[False for _ in range(GRID_W)] for _ in range(GRID_H)]
            r2 = reveal_r * reveal_r
            for sid, pdata in list(players.items()):
                st = player_state.get(sid)
                if not st:
                    continue
                cx, cy = st.get('cell', (0, 0))
                # ensure seen exists
                if 'seen' not in st or not st['seen']:
                    st['seen'] = [[False for _ in range(GRID_W)] for _ in range(GRID_H)]
                seen = st['seen']
                # reveal around current cell
                x0 = max(0, cx - reveal_r)
                x1 = min(GRID_W - 1, cx + reveal_r)
                y0 = max(0, cy - reveal_r)
                y1 = min(GRID_H - 1, cy + reveal_r)
                for yy in range(y0, y1 + 1):
                    dy = yy - cy
                    for xx in range(x0, x1 + 1):
                        dx = xx - cx
                        if dx*dx + dy*dy <= r2:
                            seen[yy][xx] = True
            # Build combined visibility from union of all players' seen masks
            for st in player_state.values():
                sm = st.get('seen')
                if not sm:
                    continue
                for yy in range(GRID_H):
                    row_sm = sm[yy]
                    row_vis = visible_mask[yy]
                    for xx in range(GRID_W):
                        if row_sm[xx]:
                            row_vis[xx] = True
        else:
            # full visibility
            visible_mask = [[True for _ in range(GRID_W)] for _ in range(GRID_H)]

        # Draw biomes background on empty tiles and walls in white BEFORE players so they are not covered
        biome_colors = {
            0: (16, 16, 16),
            1: (255, 179, 186),  # pastel red
            2: (255, 223, 186),  # pastel orange
            3: (255, 255, 186),  # pastel yellow
            4: (186, 255, 201),  # pastel green
            5: (186, 225, 255),  # pastel blue
            6: (218, 186, 255),  # pastel purple
        }
        # Advance simple enemy AI/movement
        tick_enemies()
        # Fill empty cells with blended biome color if overlapping
        for y in range(GRID_H):
            for x in range(GRID_W):
                if grid[y][x] == EMPTY:
                    tx, ty = cell_to_px(x, y)
                    if not visible_mask[y][x]:
                        # unseen tiles are dark
                        pygame.draw.rect(screen, (8, 8, 8), (tx, ty, TILE_SIZE, TILE_SIZE))
                        continue
                    # Blend colors from all centers within radius using inverse-distance weights
                    if biome_centers and biome_radius > 0:
                        cx_sum = cy_sum = 0  # not used; compute weights only
                        wt_sum = 0.0
                        r = biome_radius
                        r2 = r * r
                        cr = cg = cb = 0.0
                        for (cx, cy, bid) in biome_centers:
                            dx = x - cx
                            dy = y - cy
                            d2 = dx*dx + dy*dy
                            if d2 <= r2:
                                # weight inversely proportional to distance (avoid div by 0)
                                w = 1.0 / (1.0 + math.sqrt(max(0.0, d2)))
                                base = biome_colors.get(bid, (16, 16, 16))
                                cr += base[0] * w
                                cg += base[1] * w
                                cb += base[2] * w
                                wt_sum += w
                        if wt_sum > 0:
                            col = (int(cr / wt_sum), int(cg / wt_sum), int(cb / wt_sum))
                        else:
                            bid = biomes[y][x] if biomes else 0
                            col = biome_colors.get(bid, (16, 16, 16))
                    else:
                        bid = biomes[y][x] if biomes else 0
                        col = biome_colors.get(bid, (16, 16, 16))
                    pygame.draw.rect(screen, col, (tx, ty, TILE_SIZE, TILE_SIZE))
        # Draw walls on top in white
        wall_color = (255, 255, 255)
        for y in range(GRID_H):
            for x in range(GRID_W):
                if grid[y][x] == WALL:
                    tx, ty = cell_to_px(x, y)
                    if visible_mask[y][x]:
                        pygame.draw.rect(screen, wall_color, (tx, ty, TILE_SIZE, TILE_SIZE))
                    else:
                        pygame.draw.rect(screen, (8, 8, 8), (tx, ty, TILE_SIZE, TILE_SIZE))

        # Enemy rendering and radar pings (pings on top), gated by config
        try:
            show_enemies = bool(vis_cfg.get('enemies', True))
            show_pings = bool(vis_cfg.get('enemy_pings', True))
            pings_ignore_vis = bool(vis_cfg.get('enemy_pings_ignore_visibility', False))
        except Exception:
            show_enemies, show_pings, pings_ignore_vis = True, True, False
        if show_enemies:
            render_enemies(screen, visible_mask)
        if show_pings:
            render_enemy_pings(screen, None if pings_ignore_vis else visible_mask)

        # Create local state for new players, and process their pending command
        list_y = 220
        # Snapshot enemy occupancy for player collision checks
        e_occ_for_players = enemy_occupied_cells()
        for sid, pdata in list(players.items()):
            ensure_player(sid)
            cmd = pdata.get('pending')
            if cmd:
                # Rotation or forward/backward translation based on facing angle
                cx, cy = player_state[sid]['cell']
                ang = player_state[sid].get('angle', math.radians(90))
                targ = player_state[sid].get('target_angle', ang)

                def angle_to_dir(a: float) -> str:
                    # Map angle to nearest cardinal for 4x4 white pixels indicator
                    a = (a + 2*math.pi) % (2*math.pi)
                    if a < math.pi/4 or a >= 7*math.pi/4:
                        return 'right'
                    if a < 3*math.pi/4:
                        return 'down'
                    if a < 5*math.pi/4:
                        return 'left'
                    return 'up'

                moved = False
                if cmd in ('left', 'right'):
                    # queue a 90° turn by adjusting target_angle; smooth interp happens each frame
                    if cmd == 'left':
                        targ -= ROT_STEP
                    else:
                        targ += ROT_STEP
                    # keep target within [-pi, pi] for stability
                    while targ <= -math.pi:
                        targ += 2*math.pi
                    while targ > math.pi:
                        targ -= 2*math.pi
                    player_state[sid]['target_angle'] = targ
                else:
                    # Translation commands: instant tile step based on facing angle
                    dx = dy = 0
                    if cmd in ('up', 'down'):
                        # forward/back relative to angle; allow 8-directional steps
                        forward = 1 if cmd == 'up' else -1
                        vx = math.cos(ang) * forward
                        vy = math.sin(ang) * forward
                        dx = 1 if vx > 0.5 else (-1 if vx < -0.5 else 0)
                        dy = 1 if vy > 0.5 else (-1 if vy < -0.5 else 0)
                    elif cmd in ('strafe_left', 'strafe_right'):
                        # sidestep perpendicular to facing; allow 8-directional steps
                        s = math.sin(ang)
                        c = math.cos(ang)
                        # Screen coords: +x right, +y down. Player-left uses ( +sin, -cos ).
                        if cmd == 'strafe_left':
                            vx, vy = (s), (-c)
                        else:  # 'strafe_right'
                            vx, vy = (-s), (c)
                        dx = 1 if vx > 0.5 else (-1 if vx < -0.5 else 0)
                        dy = 1 if vy > 0.5 else (-1 if vy < -0.5 else 0)
                    # No strafe on left/right; those are turns
                    nx, ny = cx + dx, cy + dy
                    # Bounds and collisions
                    if dx != 0 or dy != 0:
                        if 0 <= nx < GRID_W and 0 <= ny < GRID_H:
                            if (grid[ny][nx] != WALL
                                and (nx, ny) not in occupied
                                and (nx, ny) not in solid_cells
                                and (nx, ny) not in e_occ_for_players):
                                occupied.pop((cx, cy), None)
                                occupied[(nx, ny)] = sid
                                player_state[sid]['cell'] = (nx, ny)
                                player_state[sid]['pos'] = cell_to_px(nx, ny)
                                moved = True
                pdata['pending'] = None  # consume the command

            # Smoothly rotate towards target angle every frame
            ang = player_state[sid].get('angle', math.radians(90))
            targ = player_state[sid].get('target_angle', ang)
            # compute shortest angular difference to target
            diff = (targ - ang + math.pi) % (2*math.pi) - math.pi
            if abs(diff) > 1e-4:
                # advance angle towards target by ROT_SPEED * dt
                dt = clock.get_time() / 1000.0
                step = ROT_SPEED * dt
                if abs(diff) <= step:
                    ang = targ
                else:
                    ang += step if diff > 0 else -step
                # normalize to [-pi, pi]
                if ang <= -math.pi:
                    ang += 2*math.pi
                if ang > math.pi:
                    ang -= 2*math.pi
                player_state[sid]['angle'] = ang
            # Update facing dir continuously for indicator
            def angle_to_dir(a: float) -> str:
                a = (a + 2*math.pi) % (2*math.pi)
                if a < math.pi/4 or a >= 7*math.pi/4:
                    return 'right'
                if a < 3*math.pi/4:
                    return 'down'
                if a < 5*math.pi/4:
                    return 'left'
                return 'up'
            player_state[sid]['dir'] = angle_to_dir(player_state[sid]['angle'])

            # Mirror live state back to server players dict for persistence
            try:
                st = player_state.get(sid) or {}
                p = players.get(sid)
                if p is not None:
                    p['cell'] = tuple(st.get('cell') or (0, 0))
                    p['angle'] = float(st.get('angle', ang))
                    if 'seen' in st and st['seen']:
                        p['seen'] = st['seen']
            except Exception:
                pass

            # Process pending hand action (e.g., pickaxe breaking a wall)
            act = players.get(sid, {}).pop('pending_action', None)
            if act in ('left', 'right'):
                # Determine equipped item in that hand
                eq = (players.get(sid, {}).get('equipment') or {})
                item_id = eq.get(f'{act}_hand')
                it = ITEM_DB.get(item_id or '') or {}
                stats = it.get('stats') or {}
                wall_damage = int(stats.get('wall_damage', 0) or 0)
                if wall_damage > 0:
                    # Target the tile directly in front of the player based on facing dir
                    cx, cy = player_state[sid]['cell']
                    d = player_state[sid].get('dir', 'down')
                    dx, dy = 0, 0
                    if d == 'up':
                        dy = -1
                    elif d == 'down':
                        dy = 1
                    elif d == 'left':
                        dx = -1
                    elif d == 'right':
                        dx = 1
                    tx, ty = cx + dx, cy + dy
                    did_hit = False
                    if 0 <= tx < GRID_W and 0 <= ty < GRID_H:
                        if grid[ty][tx] == WALL:
                            # apply damage to wall hp (use per-tile max via biome)
                            try:
                                bid_hit = int(biomes[ty][tx])
                            except Exception:
                                bid_hit = 0
                            max_loc = max(1, int(WALL_HP_BASE + WALL_HP_PER_BIOME * bid_hit))
                            if wall_hp[ty][tx] <= 0:
                                wall_hp[ty][tx] = max_loc
                            wall_hp[ty][tx] = max(0, wall_hp[ty][tx] - wall_damage)
                            if wall_hp[ty][tx] <= 0:
                                grid[ty][tx] = EMPTY
                                wall_hp[ty][tx] = 0
                            did_hit = True

                    # Emit a simple FX event on successful hit (client will render overlay)
                    if did_hit:
                        try:
                            # level based on remaining hp ratio (0..1), inverted to show stronger cracks when low hp
                            rem = wall_hp[ty][tx] if (0 <= tx < GRID_W and 0 <= ty < GRID_H) else 0
                            try:
                                bid_hit = int(biomes[ty][tx])
                            except Exception:
                                bid_hit = 0
                            max_loc = max(1, int(WALL_HP_BASE + WALL_HP_PER_BIOME * bid_hit))
                            ratio = 1.0 - (float(rem) / float(max_loc) if max_loc > 0 else 1.0)
                            socketio.emit('fx', { 'type': 'crack', 'cell': [int(tx), int(ty)], 'level': max(0.0, min(1.0, ratio)) }, to=sid)
                            socketio.emit('fx', { 'type': 'hit_spark' }, to=sid)
                        except Exception:
                            pass

                    # Durability: decrement on a successful hit, then unequip if broken
                    if did_hit and item_id:
                        pdata = players.get(sid, {})
                        dur = (pdata.setdefault('durability', {}) or {}).get(f'{act}_hand')
                        if dur is None:
                            # initialize from item stats or default 1
                            dur = int((stats.get('durability') or 1))
                        # Decrement by 1 per hit for now
                        dur = int(dur) - 1
                        pdata['durability'][f'{act}_hand'] = max(0, dur)
                        if dur <= 0:
                            # Try to move item to backpack; else drop near player
                            stored = try_add_to_backpack(pdata, item_id)
                            if not stored:
                                drop_item_near(cx, cy, item_id)
                            # Unequip
                            eq[f'{act}_hand'] = None
                            pdata['durability'].pop(f'{act}_hand', None)
                            # Notify client of equipment change (HUD)
                            try:
                                socketio.emit('equip', {'equipment': {k: v for k, v in eq.items()}}, to=sid)
                            except Exception:
                                pass

            # draw player square and facing highlight
            px, py = player_state[sid]['pos']
            pygame.draw.rect(screen, (0, 200, 255), (px, py, PLAYER_SIZE, PLAYER_SIZE))
            # leading 2 pixels white based on facing
            d = player_state[sid].get('dir', 'down')
            white = (255, 255, 255)
            if d == 'up':
                screen.set_at((px + 1, py + 0), white)
                screen.set_at((px + 2, py + 0), white)
            elif d == 'down':
                screen.set_at((px + 1, py + 3), white)
                screen.set_at((px + 2, py + 3), white)
            elif d == 'left':
                screen.set_at((px + 0, py + 1), white)
                screen.set_at((px + 0, py + 2), white)
            elif d == 'right':
                screen.set_at((px + 3, py + 1), white)
                screen.set_at((px + 3, py + 2), white)

            # radar ping overlay at player position
            try:
                t = time.time()
                # pulse radius 4..20 px
                r = 4 + int((math.sin(t * 2.0) + 1.0) * 0.5 * 16)
                surf = pygame.Surface((r*2 + 4, r*2 + 4), pygame.SRCALPHA)
                alpha = 140
                pygame.draw.circle(surf, (0, 255, 255, alpha), (r+2, r+2), r, width=2)
                # center over player tile
                screen.blit(surf, (px + PLAYER_SIZE//2 - (r+2), py + PLAYER_SIZE//2 - (r+2)))
            except Exception:
                pass

            # sidebar listing
            name = pdata.get('name', 'Player')
            screen.blit(font.render(name, True, (200, 200, 200)), (20, list_y))
            list_y += 28

        

        # Draw entity markers (green dots) for anything placed in the world (not players/walls)
        dot_color = (50, 220, 50)
        for ent in world_entities:
            pos = ent.get('pos') or ent.get('position')
            if not pos or len(pos) < 2:
                continue
            ex, ey = float(pos[0]), float(pos[1])
            # Hide entities in unseen tiles when fog is enabled
            ix, iy = int(ex), int(ey)
            if not (0 <= iy < GRID_H and 0 <= ix < GRID_W and visible_mask[iy][ix]):
                continue
            # convert grid coords (floats) to pixel space
            px = BOARD_ORIGIN_X + int(ex * TILE_SIZE)
            py = BOARD_ORIGIN_Y + int(ey * TILE_SIZE)
            # 2x2 green dot centered-ish
            pygame.draw.rect(screen, dot_color, (px - 1, py - 1, 2, 2))

        # Emit simple raycast frames to each player at ~10 FPS
        now = time.time()
        for sid, pdata in list(players.items()):
            st = player_state.get(sid)
            if not st:
                continue
            if now - st.get('last_frame_ts', 0.0) < 0.1:
                continue
            st['last_frame_ts'] = now

            cx, cy = st['cell']
            # player center in cell space
            px = cx + 0.5
            py = cy + 0.5
            angle = st.get('angle', math.radians(90))

            heights = [0] * RC_NUM_RAYS
            shades = [0] * RC_NUM_RAYS
            dists = [0.0] * RC_NUM_RAYS

            for r in range(RC_NUM_RAYS):
                # ray angle across FOV
                ray_ang = angle - RC_FOV / 2 + (r / (RC_NUM_RAYS - 1)) * RC_FOV
                ray_dir_x = math.cos(ray_ang)
                ray_dir_y = math.sin(ray_ang)

                map_x = int(px)
                map_y = int(py)

                delta_dist_x = abs(1.0 / ray_dir_x) if ray_dir_x != 0 else 1e9
                delta_dist_y = abs(1.0 / ray_dir_y) if ray_dir_y != 0 else 1e9

                if ray_dir_x < 0:
                    step_x = -1
                    side_dist_x = (px - map_x) * delta_dist_x
                else:
                    step_x = 1
                    side_dist_x = (map_x + 1.0 - px) * delta_dist_x
                if ray_dir_y < 0:
                    step_y = -1
                    side_dist_y = (py - map_y) * delta_dist_y
                else:
                    step_y = 1
                    side_dist_y = (map_y + 1.0 - py) * delta_dist_y

                hit = 0
                side = 0  # 0: x side, 1: y side
                depth = 0.0
                # DDA loop
                while hit == 0 and depth < RC_MAX_DIST:
                    if side_dist_x < side_dist_y:
                        side_dist_x += delta_dist_x
                        map_x += step_x
                        side = 0
                    else:
                        side_dist_y += delta_dist_y
                        map_y += step_y
                        side = 1
                    if 0 <= map_x < GRID_W and 0 <= map_y < GRID_H:
                        if grid[map_y][map_x] == WALL:
                            hit = 1
                    else:
                        hit = 1  # out of bounds treated as wall

                if side == 0:
                    perp_dist = (side_dist_x - delta_dist_x)
                else:
                    perp_dist = (side_dist_y - delta_dist_y)

                # Protect against zero
                perp_dist = max(1e-4, perp_dist)

                # Column height proportional to inverse distance; boosted 1.5x
                col_h = int(1.5 * RC_H / perp_dist)
                col_h = max(1, min(RC_H, col_h))

                # Distance shading (closer = brighter)
                s = 1.0 / (1.0 + 0.08 * perp_dist)
                if side == 1:
                    s *= 0.85  # darken for y-sides
                s = max(0.15, min(1.0, s))

                # Additional darkening to simulate cracks based on wall HP
                if 0 <= map_x < GRID_W and 0 <= map_y < GRID_H and grid[map_y][map_x] == WALL:
                    hp = wall_hp[map_y][map_x]
                    # compute local max based on biome
                    try:
                        bid_loc = int(biomes[map_y][map_x])
                    except Exception:
                        bid_loc = 0
                    max_loc = max(1, int(WALL_HP_BASE + WALL_HP_PER_BIOME * bid_loc))
                    frac = max(0.0, min(1.0, hp / float(max_loc)))
                    # Healthy -> 1.0; broken -> 0.6
                    s *= (0.6 + 0.4 * frac)

                heights[r] = col_h
                shades[r] = int(255 * s)
                dists[r] = float(perp_dist)

            # Build billboard sprites from world entities (distance-scaled)
            sprites: List[Dict[str, Any]] = []
            # Projection helpers
            def angle_diff(a, b):
                d = (a - b + math.pi) % (2*math.pi) - math.pi
                return d

            for ent in world_entities:
                # For now, only render items on phone; skip enemies/others
                if (ent.get('type') or 'item') != 'item':
                    continue
                pos = ent.get('pos') or ent.get('position')
                if not pos or len(pos) < 2:
                    continue
                ex, ey = float(pos[0]), float(pos[1])
                dx = ex - px
                dy = ey - py
                dist = math.hypot(dx, dy)
                if dist <= 1e-3:
                    continue
                ang_to = math.atan2(dy, dx)
                rel = angle_diff(ang_to, angle)
                # Cull outside FOV (+ small margin)
                if abs(rel) > (RC_FOV/2 + math.radians(10)):
                    continue

                # Map rel angle to screen column center
                norm = (rel + RC_FOV/2) / RC_FOV  # 0..1 across FOV
                ray_x = int(norm * (RC_NUM_RAYS - 1))
                ray_x = max(0, min(RC_NUM_RAYS - 1, ray_x))

                spr = ent.get('sprite', {})
                base_w = int(spr.get('base_width', 64))
                base_h = int(spr.get('base_height', 64))
                scale = float(spr.get('scale', 1.0))
                y_off = int(spr.get('y_offset', 0))

                # screen height for sprite
                base = RC_H / max(1e-3, dist)
                # Item/enemy sprite height; items rendered 50% smaller globally
                type_str = (ent.get('type') or 'item')
                item_scale_mult = 0.5 if type_str == 'item' else 1.0
                out_h = int(base * (base_h / 64.0) * scale * item_scale_mult)
                out_h = max(1, min(3 * RC_H, out_h))
                aspect = base_w / max(1, base_h)
                out_w = int(out_h * aspect)
                center_x = ray_x
                x = center_x - out_w // 2
                y = (RC_H - out_h) // 2 - y_off
                # Anchor items so their vertical center sits on the floor line (screen center)
                # This avoids floating while not pinning to the ceiling; add slight bias into floor
                if type_str == 'item':
                    y = (RC_H // 2) - (out_h // 2) - y_off
                    y += 4

                # Sprite sheet or single image
                if 'sheet' in spr:
                    sheet = spr['sheet']
                    directions = int(spr.get('directions', 8))
                    states = spr.get('states', {})
                    state = ent.get('state', 'idle')
                    st_def = states.get(state) or states.get('idle')
                    if not st_def:
                        continue
                    frames = st_def.get('frames', [])
                    frame_ms = int(st_def.get('frame_ms', 180))
                    if not frames:
                        continue
                    # advance animation
                    # store per-entity local time
                    ent['anim_t'] = ent.get('anim_t', 0.0) + (now - st.get('last_frame_ts', now))
                    total_ms = max(1, frame_ms * len(frames))
                    t_ms = int((ent['anim_t'] * 1000) % total_ms)
                    idx = min(len(frames) - 1, t_ms // frame_ms)
                    fc, fr = frames[idx]
                    sx = int(fc) * base_w
                    sy = int(fr) * base_h
                    sprites.append({
                        'img': f'enemies/{sheet}', 'sx': sx, 'sy': sy, 'sw': base_w, 'sh': base_h,
                        'x': x, 'y': y, 'w': out_w, 'h': out_h, 'depth': dist
                    })
                else:
                    image = spr.get('image')
                    if not image:
                        continue
                    sprites.append({
                        'img': f'{image}', 'sx': 0, 'sy': 0, 'sw': base_w, 'sh': base_h,
                        'x': x, 'y': y, 'w': out_w, 'h': out_h, 'depth': dist
                    })

            # Add enemy sprites (PNG) so phones render enemies
            etypes = get_enemy_type_map()
            for e in enemies.values():
                pos = e.get('pos')
                if not pos or len(pos) < 2:
                    continue
                ex, ey = float(pos[0]), float(pos[1])
                dx = ex - px
                dy = ey - py
                dist = math.hypot(dx, dy)
                if dist <= 1e-3:
                    continue
                ang_to = math.atan2(dy, dx)
                rel = angle_diff(ang_to, angle)
                if abs(rel) > (RC_FOV/2 + math.radians(10)):
                    continue
                norm = (rel + RC_FOV/2) / RC_FOV
                ray_x = int(norm * (RC_NUM_RAYS - 1))
                ray_x = max(0, min(RC_NUM_RAYS - 1, ray_x))

                info = etypes.get(str(e.get('type', ''))) or {}
                image = info.get('image')
                if not image:
                    continue
                base_w = 64
                base_h = 64
                scale = 1.0
                y_off = 0

                base = RC_H / max(1e-3, dist)
                out_h = int(base * (base_h / 64.0) * scale)
                out_h = max(1, min(3 * RC_H, out_h))
                aspect = base_w / max(1, base_h)
                out_w = int(out_h * aspect)
                center_x = ray_x
                x = center_x - out_w // 2
                y = (RC_H - out_h) // 2 - y_off

                sprites.append({
                    'img': f'items/{image}', 'sx': 0, 'sy': 0, 'sw': base_w, 'sh': base_h,
                    'x': x, 'y': y, 'w': out_w, 'h': out_h, 'depth': dist
                })

            # Determine sky colour for this player based on their biome
            pcx, pcy = player_state[sid]['cell']
            cx_i, cy_i = int(pcx), int(pcy)
            sky_r, sky_g, sky_b = biome_sky_colour_at(cx_i, cy_i)
            try:
                bid = int(biomes[cy_i][cx_i])
            except Exception:
                bid = 0

            socketio.emit('frame', {
                'w': RC_W,
                'h': RC_H,
                'heights': heights,
                'shades': shades,
                'dists': dists,
                'sprites': sorted(sprites, key=lambda s: -s['depth']),
                'sky': [int(sky_r), int(sky_g), int(sky_b)],
                'biome': int(bid),
                'angle': float(player_state.get(sid, {}).get('angle', angle)),
            }, to=sid)

        pygame.display.flip()
        clock.tick(30)

    pygame.quit()
