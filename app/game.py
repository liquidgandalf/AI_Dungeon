from __future__ import annotations

def _load_scaled_image(path: str, w: int, h: int) -> 'pygame.Surface':
    key = (path, w, h)
    surf = _ENEMY_SPRITE_CACHE.get(key)
    if surf is not None:
        return surf
    try:
        img = pygame.image.load(path).convert_alpha()
        surf = pygame.transform.smoothscale(img, (w, h))
        _ENEMY_SPRITE_CACHE[key] = surf
        return surf
    except Exception:
        return None


def _resolve_enemy_image_file(img_name: str) -> str:
    # enemy_types.json 'image' is a filename; assets live in static/img/items/
    return os.path.join('static', 'img', 'items', img_name)


def _get_enemy_sprite(etype: str, info: Dict[str, Any]) -> Tuple['pygame.Surface', int, int]:
    """Return (sprite_surface, w, h) or (None, 0, 0) if not available.
    Bosses are rendered larger (~128x256). Slimes at 64x64.
    """
    img_name = info.get('image')
    if not img_name:
        return (None, 0, 0)
    is_boss = bool(info.get('boss'))
    tier = (info.get('tier') or '').lower()
    # Size policy
    if is_boss:
        w, h = 128, 256
        if tier == 'super':
            w, h = 144, 288  # slightly bigger
    else:
        w, h = 64, 64
    path = _resolve_enemy_image_file(img_name)
    surf = _load_scaled_image(path, w, h)
    if surf is None:
        return (None, 0, 0)
    return (surf, w, h)


# Cache natural image sizes to avoid reloading every frame
_IMAGE_SIZE_CACHE = {}

def _get_image_natural_size(img_name: str):
    """Return (w, h) read from the PNG at static/img/items/<img_name>.
    Falls back to (64,64) on error. Results are cached.
    """
    try:
        path = _resolve_enemy_image_file(img_name)
    except Exception:
        return (64, 64)
    wh = _IMAGE_SIZE_CACHE.get(path)
    if wh:
        return wh
    try:
        img = pygame.image.load(path)
        wh = (int(img.get_width()), int(img.get_height()))
    except Exception:
        wh = (64, 64)
    _IMAGE_SIZE_CACHE[path] = wh
    return wh


def _get_item_icon(icon_name: str, w: int = 16, h: int = 16) -> 'pygame.Surface':
    key = (icon_name, w, h)
    surf = _ITEM_ICON_CACHE.get(key)
    if surf is not None:
        return surf
    try:
        # Icons stored under static/img/items, icon_name may already include subdir
        path = icon_name
        if not (os.path.sep in icon_name or '/' in icon_name):
            path = os.path.join('static', 'img', 'items', icon_name)
        img = pygame.image.load(path).convert_alpha()
        surf = pygame.transform.smoothscale(img, (w, h))
        _ITEM_ICON_CACHE[key] = surf
        return surf
    except Exception:
        return None

# SkeletonGame/app/game.py
import random
import copy
import time
import math
import os
import pygame
import hashlib
from typing import Dict, Tuple, List, Any
from app.server import players, socketio
from app import config as game_config
from app.items import ITEM_DB, get_weight, backpack_capacity, register_item, get_item
from app import enemy_ai

# --- Rendering tuning helpers ---
def _sample_curve(points: List[List[float]] | List[Tuple[float, float]], x: float, default: float = 1.0) -> float:
    """Sample a piecewise-linear curve defined by [[x0, y0], [x1, y1], ...].
    - If points is missing/invalid, return default.
    - If x is below first knot, return y0; if above last, return y_last.
    - Otherwise, linearly interpolate between surrounding knots.
    """
    try:
        pts = list(points or [])
        if not pts:
            return float(default)
        # Ensure sorted by x
        pts = sorted([(float(px), float(py)) for (px, py) in pts], key=lambda p: p[0])
        if x <= pts[0][0]:
            return float(pts[0][1])
        if x >= pts[-1][0]:
            return float(pts[-1][1])
        # Find segment
        for i in range(1, len(pts)):
            x0, y0 = pts[i-1]
            x1, y1 = pts[i]
            if x0 <= x <= x1:
                t = 0.0 if x1 == x0 else (float(x) - x0) / (x1 - x0)
                return float(y0 + t * (y1 - y0))
        return float(default)
    except Exception:
        return float(default)

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
# Generated knowledge scrolls distribution queue
SCROLL_QUEUE: List[str] = []
SCROLLS_GENERATED: bool = False
# Pillar spawn control
PILLARS_SPAWNED: bool = False
# One-time QA pillar at start area control
START_PILLAR_PLACED: bool = False
# One-time QA test items near start control
TEST_ITEMS_SPAWNED: bool = False
# World log flag
WORLD_LOG_WRITTEN: bool = False
# Cells blocked by solid entities (e.g., items/props/spawners)
solid_cells: set = set()

# Enemy instances and occupancy
enemies: Dict[str, Dict[str, Any]] = {}
random_enemies_inited: bool = False
# Persisted room metadata for logging/QA
ROOMS: List[Dict[str, Any]] = []

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
_WALL_TYPE_MAP: Dict[str, Dict[str, Any]] = {}
# Sprite caches
_ENEMY_SPRITE_CACHE: Dict[Tuple[str, int, int], pygame.Surface] = {}
_ITEM_ICON_CACHE: Dict[Tuple[str, int, int], pygame.Surface] = {}

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
    # Determine sprite sizing for clients
    is_boss = bool(tdef.get('boss'))
    tier = (tdef.get('tier') or '').lower()
    if is_boss:
        spr_w, spr_h = (144, 288) if tier == 'super' else (128, 256)
    else:
        spr_w, spr_h = (64, 64)

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
        # Client sprite hints
        'sprite_w': spr_w,
        'sprite_h': spr_h,
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

def get_wall_type_map() -> Dict[str, Dict[str, Any]]:
    """Load and cache wall type definitions keyed by 'type'."""
    global _WALL_TYPE_MAP
    if not _WALL_TYPE_MAP:
        try:
            types = game_config.get_wall_types()
            _WALL_TYPE_MAP = {t.get('type'): t for t in types if t.get('type')}
        except Exception:
            _WALL_TYPE_MAP = {}
    return _WALL_TYPE_MAP


def render_enemy_pings(screen: pygame.surface.Surface, visible: List[List[bool]] = None):
    """Draw pulsing radar pings at enemy positions using their type pingcolour.
    On by default; later can be gated by configs or player items.
    """
    if not enemies:
        return
    types = get_enemy_type_map()
    t = time.time()
    # Pre-make surface per distinct colour and size to reduce overdraw setup
    surf_cache: Dict[Tuple[int,int,int,int,int], pygame.Surface] = {}

    for e in enemies.values():
        etype = str(e.get('type', ''))
        info = types.get(etype) or {}
        col = info.get('pingcolour', [255, 0, 255])  # magenta default
        # Boss scale: sub boss x1.6, super boss x2.2
        is_boss = bool(info.get('boss'))
        tier = (info.get('tier') or '').lower()
        scale = 1.0
        if is_boss:
            scale = 2.2 if tier == 'super' else 1.6
        # pulse radius in pixels (base 4..16)
        base_r = 4 + int((math.sin(t * 2.0) + 1.0) * 0.5 * 12)
        r = max(4, int(base_r * scale))
        size = r * 2 + 4
        color_a = (int(col[0]), int(col[1]), int(col[2]), 140)
        cache_key = (color_a[0], color_a[1], color_a[2], color_a[3], r)
        surf = surf_cache.get(cache_key)
        if surf is None:
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.circle(surf, color_a, (r+2, r+2), r, width=2)
            surf_cache[cache_key] = surf
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


def _special_item_ids() -> List[str]:
    try:
        return [iid for iid, it in ITEM_DB.items() if bool((it or {}).get('special'))]
    except Exception:
        return []


def _enemy_type_lists() -> Tuple[List[str], List[str], List[str]]:
    """Return (normal_types, sub_boss_types, big_boss_types)."""
    types = get_enemy_type_map()
    normal, sub_boss, big_boss = [], [], []
    for t_id, info in types.items():
        if not t_id:
            continue
        if bool(info.get('boss')):
            if (info.get('tier') or '').lower() == 'super':
                big_boss.append(t_id)
            else:
                sub_boss.append(t_id)
        else:
            normal.append(t_id)
    return normal, sub_boss, big_boss


def init_random_enemies_once():
    """Spawn a configurable number of random enemies scattered across the map.
    Uses game_config.spawns.random_enemies. Places only on EMPTY tiles, not in
    solid item cells, and avoids overlapping other enemies.
    """
    global random_enemies_inited
    if random_enemies_inited:
        return
    # We will compute our own counts based on special-item and boss rules
    try:
        cfg = game_config.get_game_config()
    except Exception:
        cfg = {}
    types = game_config.get_enemy_types()
    type_ids = [t.get('type') for t in types if t.get('type')]
    if not type_ids:
        return
    occ = set(enemy_occupied_cells().keys())

    # Build type groups and special item pool
    normal_types, sub_boss_types, big_boss_types = _enemy_type_lists()
    special_items = _special_item_ids()
    random.shuffle(special_items)

    # Spawn all sub bosses and big bosses once
    spawned = 0
    def _spawn_of_type(tid: str) -> Dict[str, Any]:
        # find empty cell
        x, y = random_empty_cell()
        inst = make_enemy_instance(tid, x, y, spawner_id=None)
        enemies[inst['id']] = inst
        occ.add((x, y))
        return inst

    # 1) Spawn 18 slimes, each carrying one unique special item (green ping)
    slime_types = [t for t in normal_types if t.startswith('slime_')]
    if not slime_types:
        # fallback: treat all normal types as slime candidates
        slime_types = list(normal_types)
    carry_items = list(special_items[:18])
    # Ensure we have up to 18
    carry_items = carry_items[:18]
    for i, itm in enumerate(carry_items):
        tid = slime_types[i % max(1, len(slime_types))] if slime_types else None
        if not tid:
            break
        inst = _spawn_of_type(tid)
        spawned += 1
        inst['carried_items'] = [itm]
        inst['carried_item'] = itm
        # Green ping for item carriers (non-boss)
        inst['pingcolour'] = [60, 220, 60]

    # 2) Spawn all bosses (sub=yellow, big=red)
    all_special = _special_item_ids()
    # Prepare big-boss global uniqueness for affinities
    used_big_affinity: set = set()
    # Sub bosses: allocate fear/desire/vulnerable (can overlap globally)
    for tid in sub_boss_types:
        inst = _spawn_of_type(tid)
        spawned += 1
        inst['pingcolour'] = [255, 255, 0]
        # Assign 3 distinct items for affinities
        pool = list(all_special)
        random.shuffle(pool)
        picks = pool[:3]
        inst['affinities'] = {
            'fear': (picks[0] if len(picks) > 0 else None),
            'desire': (picks[1] if len(picks) > 1 else None),
            'vulnerable': (picks[2] if len(picks) > 2 else None),
        }
    # Big bosses: allocate 3 items each with no overlap across big bosses
    for tid in big_boss_types:
        inst = _spawn_of_type(tid)
        spawned += 1
        inst['pingcolour'] = [255, 60, 60]
        # Build pool excluding used
        pool = [i for i in all_special if i not in used_big_affinity]
        random.shuffle(pool)
        picks = pool[:3] if len(pool) >= 3 else pool
        for p in picks:
            used_big_affinity.add(p)
        inst['affinities'] = {
            'fear': (picks[0] if len(picks) > 0 else None),
            'desire': (picks[1] if len(picks) > 1 else None),
            'vulnerable': (picks[2] if len(picks) > 2 else None),
        }

    # For this test mode, do not add extra random enemies; total is 18 slimes + all bosses
    random_enemies_inited = True


def _boss_instances_by_type() -> Dict[str, Dict[str, Any]]:
    """Return a mapping etype -> one representative boss instance for that type."""
    out = {}
    types = get_enemy_type_map()
    for inst in enemies.values():
        tid = str(inst.get('type') or '')
        if not tid or tid in out:
            continue
        tdef = types.get(tid) or {}
        if bool(tdef.get('boss')):
            out[tid] = inst
    return out


def _register_scroll_item(scroll_id: str, enemy_name: str) -> None:
    """Ensure a scroll item type exists for this id."""
    if ITEM_DB.get(scroll_id):
        return
    base_icon = (ITEM_DB.get('scroll_of_knowledge') or {}).get('icon') or 'items/scroll_of_knowledge.png'
    register_item({
        'id': scroll_id,
        'name': f"Scroll of Knowledge: {enemy_name}",
        'active': True,
        'allowed_slots': [],
        'spawn_type': None,
        'icon': base_icon,
        'special': True,
        'stats': { 'weight': 0.2, 'durability': 1 },
        'ranged_attack': 0,
    })


def _scroll_lore_text(scroll_id: str) -> str:
    """Return the lore text for a given scroll id of form 'scroll_<etype>_<kind>'.
    Kind in {'seeks','fears','vulnerable','backstory'}.
    Substitutes placeholders using the representative boss instance affinities.
    """
    try:
        sid = str(scroll_id or '')
        # Special one-off welcome scroll shown on the start-area pillar
        if sid == 'scroll_welcome':
            return (
                "Welcome to the Labyrinth!\n\n"
                "Seek pillars of knowledge to learn the dungeon's secrets.\n"
                "Use your hands to interact; beware the denizens within."
            )
        if not sid.startswith('scroll_'):
            return ''
        parts = sid.split('_', 2)
        # Expected: ['scroll', '<etype>', '<kind>'] but etype may also contain underscores; split from right
        parts = sid.split('_')
        if len(parts) < 3:
            return ''
        kind = parts[-1]
        etype = '_'.join(parts[1:-1])
        types = get_enemy_type_map()
        tdef = types.get(etype) or {}
        # Find a representative instance to resolve affinities for substitution
        bosses = _boss_instances_by_type()
        inst = bosses.get(etype) or {}
        affin = inst.get('affinities') or {}
        def iname(iid: str) -> str:
            it = ITEM_DB.get(str(iid) or '') or {}
            return str(it.get('name', iid))
        def sub(txt: str) -> str:
            d = str(txt or '')
            return (d
                .replace('{WANTS_ITEM}', iname(affin.get('desire')) if affin.get('desire') else '')
                .replace('{HATES_ITEM}', iname(affin.get('fear')) if affin.get('fear') else '')
                .replace('{VULNERABLE_ITEM}', iname(affin.get('vulnerable')) if affin.get('vulnerable') else '')
            )
        if kind == 'seeks':
            return sub(tdef.get('description_seeks') or '')
        if kind == 'fears':
            return sub(tdef.get('description_fears') or '')
        if kind == 'vulnerable':
            return sub(tdef.get('description_vulnerable') or '')
        if kind == 'backstory':
            return str(tdef.get('backstory') or '')
        return ''
    except Exception:
        return ''


def _generate_scrolls_for_bosses() -> List[Tuple[str, str]]:
    """Create 4 scrolls per boss type (seeks, fears, vulnerable, backstory).
    Returns list of (scroll_id, element) for placement. Element is one of 'water','fire','earth', or ''.
    """
    out: List[Tuple[str, str]] = []
    types = get_enemy_type_map()
    bosses = _boss_instances_by_type()
    for etype, inst in bosses.items():
        tdef = types.get(etype) or {}
        enemy_name = tdef.get('name') or etype
        element = (tdef.get('element') or '').lower()
        for kind in ('seeks', 'fears', 'vulnerable', 'backstory'):
            sid = f"scroll_{etype}_{kind}"
            _register_scroll_item(sid, enemy_name)
            out.append((sid, element))
    return out


def _pillar_type_for_element(elem: str) -> str:
    e = (elem or '').lower()
    if e == 'water':
        return 'pillar_of_knowledge_water'
    if e == 'fire':
        return 'pillar_of_knowledge_fire'
    if e == 'earth':
        return 'pillar_of_knowledge_earth'
    return 'pillar_of_knowledge'


def _spawn_pillars_for_scrolls(scroll_elems: List[Tuple[str, str]]) -> None:
    """Spawn one pillar per (scroll_id, pillar_element) pair and pre-fill its contents with that scroll.
    Places pillars on empty, non-solid tiles. Idempotent via PILLARS_SPAWNED guard upstream.
    The element in the pair dictates which pillar variant is used, regardless of the boss element.
    """
    ents: List[Dict[str, Any]] = []
    for scroll_id, elem in scroll_elems:
        cx, cy = random_empty_cell()
        pos = [float(cx) + 0.5, float(cy) + 0.5]
        pit = _pillar_type_for_element(elem)
        # Choose sprite image from item definition icon
        itdef = ITEM_DB.get(pit) or {}
        icon = itdef.get('icon') or 'items/pillar_of_knowledge.png'
        ent = {
            'type': 'item',
            'item_id': pit,
            'pos': pos,
            # Mark as container with pre-attached contents; _attach_container_contents will only append pending scrolls if any
            'container': True,
            'contents': [ { 'item': scroll_id, 'qty': 1 } ],
            'sprite': {
                'image': icon,
                'base_width': 64,
                'base_height': 128,
                'scale': 1.0,
                # Zero offset; items are anchored to floor bottom in renderer
                'y_offset': 0,
            }
        }
        ents.append(ent)
    # Append to world and rebuild solids
    world_entities.extend(ents)
    rebuild_solid_cells()


def ensure_knowledge_pillars_once() -> None:
    """After enemies (and their affinities) are initialized, generate 40 scrolls (4 per boss)
    and spawn matching elemental pillars across the map with those scrolls.
    """
    global SCROLLS_GENERATED, PILLARS_SPAWNED
    if PILLARS_SPAWNED:
        return
    # Require enemies to exist first
    if not enemies:
        return
    # Generate scroll items per boss
    scrolls = _generate_scrolls_for_bosses()
    # Optional: also push into queue for other containers if needed
    if not SCROLLS_GENERATED:
        SCROLL_QUEUE.extend([sid for (sid, _e) in scrolls])
        SCROLLS_GENERATED = True
    # Enforce distribution: 12 water, 12 earth, 16 fire (total 40)
    target = {'water': 12, 'earth': 12, 'fire': 16}
    # Buckets by boss element
    buckets: Dict[str, List[str]] = {'water': [], 'earth': [], 'fire': [], 'other': []}
    for sid, elem in scrolls:
        e = (elem or '').lower()
        if e in ('water', 'earth', 'fire'):
            buckets[e].append(sid)
        else:
            buckets['other'].append(sid)
    # Allocate scrolls to pillars; if a bucket is short, draw from others
    alloc: List[Tuple[str, str]] = []  # (scroll_id, pillar_element)
    used: set = set()
    def take_from(bucket_name: str, need: int) -> List[str]:
        picks: List[str] = []
        pool = [s for s in buckets.get(bucket_name, []) if s not in used]
        random.shuffle(pool)
        n = min(max(0, need), len(pool))
        picks.extend(pool[:n])
        for s in picks:
            used.add(s)
        return picks
    # First, satisfy from matching buckets
    for elem_name, need in target.items():
        picks = take_from(elem_name, need)
        alloc.extend([(s, elem_name) for s in picks])
    # Top up deficits from any remaining scrolls (prefer same-element leftovers then others)
    for elem_name, need in target.items():
        have = sum(1 for _sid, e in alloc if e == elem_name)
        deficit = max(0, need - have)
        if deficit <= 0:
            continue
        # Prefer same-element leftovers first
        extra = take_from(elem_name, deficit)
        deficit -= len(extra)
        alloc.extend([(s, elem_name) for s in extra])
        if deficit > 0:
            # Pull from other + 'other'
            leftovers = [s for bucket in ('water','earth','fire','other') for s in buckets.get(bucket, []) if s not in used]
            random.shuffle(leftovers)
            extra2 = leftovers[:deficit]
            for s in extra2:
                used.add(s)
            alloc.extend([(s, elem_name) for s in extra2])
    # If we still have fewer than total target pillars due to limited scrolls, stop at available
    # Spawn pillars and pre-fill contents with allocated mapping
    _spawn_pillars_for_scrolls(alloc)
    PILLARS_SPAWNED = True


def mark_scroll_read(sid: str, scroll_id: str) -> None:
    """Record that a player has read a specific scroll id."""
    try:
        pdata = player_state.setdefault(sid, {})
        known = pdata.setdefault('knowledge', [])
        if scroll_id not in known:
            known.append(scroll_id)
    except Exception:
        pass


def get_player_knowledge(sid: str) -> List[str]:
    try:
        return list(player_state.get(sid, {}).get('knowledge') or [])
    except Exception:
        return []


def render_enemies(screen: pygame.surface.Surface, visible: List[List[bool]] = None):
    """Do not render enemy bodies on the server map; pings are rendered separately by
    render_enemy_pings(). This keeps the server display minimal while clients render sprites.
    """
    return

# Biomes grid parallel to 'grid' holding biome id per tile: 0..6
biomes: List[List[int]] = []
# Biome centers and radius used for rendering overlaps
biome_centers: List[Tuple[int,int,int]] = []  # (cx, cy, biome_id)
biome_radius: int = 0

# Wall type id per tile (string from wall_types.json); empty string for non-walls
wall_type_id: List[List[str]] = []

# Simple biome -> sky RGB palette (0..6), aligned with board biome_colors
BIOME_SKY_COLORS: Dict[int, Tuple[int,int,int]] = {
    0: (135, 206, 235),   # no biome -> sky blue
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
    global grid, biomes, wall_hp, WALL_HP_BASE, WALL_HP_PER_BIOME, wall_type_id
    if grid is not None:
        return
    # Apply deterministic seed if provided in config
    try:
        cfg = game_config.get_game_config() or {}
        seed = cfg.get('seed', None)
        if seed not in (None, ''):
            random.seed(str(seed))
    except Exception:
        pass
    # Start all walls
    g = [[WALL for _ in range(GRID_W)] for _ in range(GRID_H)]
    # Generate maze into g
    generate_maze(g, corridor_w=2, wall_w=1, room_prob=0.08)
    # Add rectangular rooms with doors before finalizing grid
    try:
        rooms_cfg = (cfg.get('rooms') or {})
        room_count = int(rooms_cfg.get('count', 12))
    except Exception:
        room_count = 12
    door_coords = add_rooms(g, room_count, size=9)
    grid = g
    # After maze, generate biomes
    biomes = generate_biomes()
    # Carve an 8x8 cleared starting area on the far-left, centered vertically
    # Keep within interior (avoid outer wall at y=0 and y=GRID_H-1)
    sx0, sx1 = 1, 8  # x in [1..8]
    sy0 = max(1, GRID_H // 2 - 4)
    sy1 = min(GRID_H - 2, sy0 + 7)
    carve_rect(grid, sx0, sy0, sx1, sy1, EMPTY)
    # Configure wall HP scaling from config (optional)
    try:
        walls_cfg = (game_config.get_game_config() or {}).get('walls') or {}
        WALL_HP_BASE = int(walls_cfg.get('hp_base', 3))
        WALL_HP_PER_BIOME = int(walls_cfg.get('hp_per_biome', 1))
    except Exception:
        WALL_HP_BASE, WALL_HP_PER_BIOME = 3, 1
    # Initialize wall type grid to default type for all wall tiles
    try:
        wt_map = get_wall_type_map()
        # pick first defined type or fallback to 'stone1'
        default_type = next(iter(wt_map.keys()), 'stone1')
    except Exception:
        wt_map = {}
        default_type = 'stone1'
    # Initialize wall hp grid using wall type durability (stats.durability)
    def _hp_max_for_type(wt_id: str) -> int:
        info = (wt_map.get(wt_id) or {})
        stats = (info.get('stats') or {})
        return max(1, int(stats.get('durability', 1) or 1))
    # Build wall type id grid and wall hp grid
    # For now all walls are default_type
    # Later we can vary by biome/region
    wall_hp = [[((_hp_max_for_type(default_type)) if grid[y][x] == WALL else 0) for x in range(GRID_W)] for y in range(GRID_H)]
    wall_type_id = [[(default_type if grid[y][x] == WALL else '') for x in range(GRID_W)] for y in range(GRID_H)]
    # Override perimeter with indestructible outer wall type if available
    outer_type = 'outer_wall' if 'outer_wall' in wt_map else None
    if outer_type is not None:
        outer_hp = _hp_max_for_type(outer_type)
        # Top and bottom rows
        y = 0
        for x in range(GRID_W):
            if grid[y][x] == WALL:
                wall_type_id[y][x] = outer_type
                wall_hp[y][x] = outer_hp
    # Apply door wall types and HP for any doors placed
    door_type = 'door1' if 'door1' in wt_map else None
    if door_type and 'door1' in wt_map:
        d_hp = _hp_max_for_type(door_type)
        for (dx, dy) in door_coords:
            if 0 <= dx < GRID_W and 0 <= dy < GRID_H and grid[dy][dx] == WALL:
                wall_type_id[dy][dx] = door_type
                wall_hp[dy][dx] = d_hp
        y = GRID_H - 1
        for x in range(GRID_W):
            if grid[y][x] == WALL:
                wall_type_id[y][x] = outer_type
                wall_hp[y][x] = outer_hp
        # Left and right columns (excluding corners already set)
        x = 0
        for y in range(1, GRID_H - 1):
            if grid[y][x] == WALL:
                wall_type_id[y][x] = outer_type
                wall_hp[y][x] = outer_hp
        x = GRID_W - 1
        for y in range(1, GRID_H - 1):
            if grid[y][x] == WALL:
                wall_type_id[y][x] = outer_type
                wall_hp[y][x] = outer_hp

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

    # Attach contents to any container items (e.g., chests) that were added
    for ent in ents:
        _attach_container_contents(ent)

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
    """Return capacity based on equipped backpack instance (resolve to type id)."""
    eq = pdata.get('equipment') or {}
    inst_id = eq.get('backpack')
    if not inst_id:
        return 0.0
    items_map = pdata.get('items') or {}
    type_id = (items_map.get(inst_id) or {}).get('type')
    return backpack_capacity(type_id) if type_id else 0.0


def try_add_instance_to_backpack(pdata: Dict[str, Any], inst_id: str) -> bool:
    """Store an item instance into inventory if there's backpack capacity.
    Computes weight from the instance's type.
    """
    cap = backpack_capacity_for_player(pdata)
    if cap <= 0:
        return False
    used = float(pdata.get('backpack_weight_used', 0.0))
    items_map = pdata.get('items') or {}
    t_id = (items_map.get(inst_id) or {}).get('type')
    if not isinstance(t_id, str):
        return False
    w = float(get_weight(t_id))
    if used + w <= cap + 1e-6:
        inv = pdata.setdefault('inventory', [])
        if inst_id not in inv:
            inv.append(inst_id)
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
        ent = {
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
        }
        _attach_container_contents(ent)
        out.append(ent)
    return out


def _roll_container_contents(item_type: str) -> List[Dict[str, Any]]:
    """Roll contents for a container item based on ITEM_DB container fields.
    Returns list of { 'item': <item_id>, 'qty': int } entries. Empty if not a container.
    """
    it = ITEM_DB.get(item_type) or {}
    if not it or not it.get('container'):
        return []
    pool = it.get('maycontain') or []
    if not isinstance(pool, list) or not pool:
        return []
    max_items = int(it.get('numberitems') or 0)
    if max_items <= 0:
        return []
    # Draw N entries with replacement; N in [1, max_items]
    draws = max(1, min(max_items, random.randint(1, max_items)))
    # Build weight list
    weights = []
    for entry in pool:
        try:
            w = float(entry.get('weight', 1))
        except Exception:
            w = 1.0
        weights.append(max(0.0, w))
    total_w = sum(weights) or 1.0
    # Normalize
    probs = [w / total_w for w in weights]
    # Helper to pick one index by cumulative probability
    import bisect
    cdf = []
    s = 0.0
    for p in probs:
        s += p
        cdf.append(s)
    def pick_idx(r: float) -> int:
        return bisect.bisect_left(cdf, r)
    out: Dict[str, int] = {}
    for _ in range(draws):
        r = random.random()
        idx = pick_idx(r)
        idx = max(0, min(len(pool) - 1, idx))
        e = pool[idx]
        try:
            qmin = int(e.get('min', 1) or 1)
            qmax = int(e.get('max', qmin) or qmin)
        except Exception:
            qmin, qmax = 1, 1
        qty = random.randint(max(1, qmin), max(1, qmax))
        item_id = str(e.get('item') or '')
        if not item_id:
            continue
        out[item_id] = out.get(item_id, 0) + qty
    return [{'item': k, 'qty': v} for k, v in out.items() if v > 0]


def _attach_container_contents(ent: Dict[str, Any]) -> None:
    """If ent is a container item (e.g., chest), roll and attach contents once."""
    try:
        if (ent.get('type') or 'item') != 'item':
            return
        item_id = str(ent.get('item_id') or '')
        if not item_id:
            return
        # Already has contents -> keep as-is
        if ent.get('container') and isinstance(ent.get('contents'), list):
            # Also try to add a pending scroll if available
            if SCROLL_QUEUE:
                ent['contents'].append({'item': SCROLL_QUEUE.pop(0), 'qty': 1})
            return
        it = ITEM_DB.get(item_id) or {}
        if not it or not it.get('container'):
            return
        ent['container'] = True
        ent['contents'] = _roll_container_contents(item_id)
        # Attach one pending scroll if available
        if SCROLL_QUEUE:
            ent['contents'].append({'item': SCROLL_QUEUE.pop(0), 'qty': 1})
    except Exception:
        return


def ensure_scrolls_generated_once() -> None:
    """Generate one 'Scroll of Knowledge' per relevant enemy type with known affinities,
    register them as items, and distribute into existing chests. Future chests will
    pull from SCROLL_QUEUE via _attach_container_contents()."""
    global SCROLLS_GENERATED
    if SCROLLS_GENERATED:
        return
    # Build enemy type map for names/texts
    types = get_enemy_type_map()
    if not enemies:
        SCROLLS_GENERATED = True
        return
    # Helper to substitute placeholders
    def _substitute(desc: str, it_name: str) -> str:
        d = str(desc or '')
        d = d.replace('{WANTS_ITEM}', it_name)
        d = d.replace('{HATES_ITEM}', it_name)
        d = d.replace('{VULNERABLE_ITEM}', it_name)
        return d
    made_ids: set = set()
    for eid, inst in list(enemies.items()):
        try:
            etype = str(inst.get('type') or '')
            if not etype or etype in made_ids:
                continue
            tdef = types.get(etype) or {}
            # Require some textual descriptors
            d_core = tdef.get('description_core')
            d_seeks = tdef.get('description_seeks')
            d_fears = tdef.get('description_fears')
            d_vuln = tdef.get('description_vulnerable')
            if not d_core:
                continue
            affin = inst.get('affinities') or {}
            # Choose one info type where affinity exists
            choice = None
            it_id = None
            if affin.get('desire') and d_seeks:
                choice = 'seeks'
                it_id = str(affin.get('desire'))
            elif affin.get('fear') and d_fears:
                choice = 'fears'
                it_id = str(affin.get('fear'))
            elif affin.get('vulnerable') and d_vuln:
                choice = 'vulnerable'
                it_id = str(affin.get('vulnerable'))
            if not choice or not it_id:
                continue
            it_info = get_item(it_id) or {}
            it_name = str(it_info.get('name') or it_id)
            # Build final text
            if choice == 'seeks':
                info_text = _substitute(d_seeks, it_name)
            elif choice == 'fears':
                info_text = _substitute(d_fears, it_name)
            else:
                info_text = _substitute(d_vuln, it_name)
            enemy_name = str(tdef.get('name') or etype)
            scroll_id = f"scroll_{etype}_{choice}"
            # Register if not present
            if not ITEM_DB.get(scroll_id):
                register_item({
                    'id': scroll_id,
                    'name': f"Scroll of Knowledge: {enemy_name}",
                    'allowed_slots': [],  # carry-only
                    'active': True,
                    'icon': 'items/Scroll of Knowledge.png',
                    'stats': { 'weight': 0.2, 'durability': 1 },
                    'special': True,
                })
                # Store derived texts on the entity for UI via description_core
                ITEM_DB[scroll_id]['description_core'] = f"{str(d_core)}\n\n{info_text}"
            SCROLL_QUEUE.append(scroll_id)
            made_ids.add(etype)
        except Exception:
            continue
    # Distribute into existing chests now
    for ent in world_entities:
        try:
            if (ent.get('type') or 'item') != 'item':
                continue
            if not str(ent.get('item_id') or '').startswith('chest_') and str(ent.get('item_id') or '') != 'chest_basic':
                continue
            _attach_container_contents(ent)
            if not SCROLL_QUEUE:
                break
        except Exception:
            continue
    SCROLLS_GENERATED = True


def write_world_log_once() -> None:
    """Write a timestamped world log under /logs/ once per run after init.
    Includes:
    - All enemies with: type/name, coords, affinities, filled descriptions, and held items if present
    - All chests with: coords and contents; for scrolls, include final description text
    """
    global WORLD_LOG_WRITTEN
    if WORLD_LOG_WRITTEN:
        return
    try:
        # Resolve base dir (project root) and logs dir
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        logs_dir = os.path.join(base_dir, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        ts = time.strftime('%d_%m_%y_%H %M %S', time.localtime())
        fname = f"world_{ts} .log"
        fpath = os.path.join(logs_dir, fname)

        # Helper: stringify affinities
        def fmt_aff(aff):
            if not isinstance(aff, dict):
                return '{}'
            return {
                'fear': aff.get('fear'),
                'desire': aff.get('desire'),
                'vulnerable': aff.get('vulnerable'),
            }

        # Helper: fill descriptions for an enemy instance/type
        types = get_enemy_type_map()
        def fill_desc(inst: Dict[str, Any]) -> Dict[str, str]:
            etype = str(inst.get('type') or '')
            tdef = types.get(etype) or {}
            d_core = str(tdef.get('description_core') or '')
            d_seeks = str(tdef.get('description_seeks') or '')
            d_fears = str(tdef.get('description_fears') or '')
            d_vuln = str(tdef.get('description_vulnerable') or '')
            affin = inst.get('affinities') or {}
            # Resolve item names from ITEM_DB
            def iname(item_id: Any) -> str:
                iid = str(item_id) if item_id is not None else ''
                it = ITEM_DB.get(iid) or {}
                nm = it.get('name') or iid
                return str(nm)
            seeks_item = iname(affin.get('desire')) if affin.get('desire') else ''
            fears_item = iname(affin.get('fear')) if affin.get('fear') else ''
            vuln_item = iname(affin.get('vulnerable')) if affin.get('vulnerable') else ''
            def sub(txt: str) -> str:
                return (txt
                    .replace('{WANTS_ITEM}', seeks_item)
                    .replace('{HATES_ITEM}', fears_item)
                    .replace('{VULNERABLE_ITEM}', vuln_item))
            return {
                'description_core': d_core,
                'description_seeks': sub(d_seeks) if d_seeks else '',
                'description_fears': sub(d_fears) if d_fears else '',
                'description_vulnerable': sub(d_vuln) if d_vuln else '',
                'backstory': str(tdef.get('backstory') or ''),
            }

        # Collect lines
        lines: List[str] = []
        lines.append('=== WORLD LOG ===')
        lines.append(f"generated_at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
        # Meta: map seed (none tracked), counts, player start cells if known
        # Seed not currently tracked anywhere
        lines.append('meta:')
        lines.append('  map_seed: null')
        # Counts
        enemy_count = len(enemies or {})
        # Classify entities to count chests/containers/items
        chest_count = 0
        other_container_count = 0
        ground_item_count = 0
        for ent in world_entities:
            try:
                if (ent.get('type') or 'item') != 'item':
                    continue
                iid = str(ent.get('item_id') or '')
                if not iid:
                    continue
                itdef = ITEM_DB.get(iid) or {}
                is_container = bool(itdef.get('container'))
                # chest detection: id startswith chest_, or name contains 'chest', or sprite image contains 'chest'
                nm = str(itdef.get('name') or iid).lower()
                spr = (ent.get('sprite') or {}).get('image') or ''
                is_chest = iid.startswith('chest_') or ('chest' in nm) or ('chest' in str(spr).lower()) or (iid == 'chest_basic')
                if is_container and is_chest:
                    chest_count += 1
                elif is_container:
                    other_container_count += 1
                else:
                    # treat as ground item if allowed_slots and not container
                    if itdef.get('allowed_slots'):
                        ground_item_count += 1
            except Exception:
                continue
        lines.append(f"  counts: {{ enemies: {enemy_count}, chests: {chest_count}, containers: {other_container_count}, ground_items: {ground_item_count} }}")
        # Rooms and doors
        try:
            if ROOMS:
                lines.append('  rooms:')
                for r in ROOMS:
                    rect = r.get('rect') or []
                    doors = r.get('doors') or []
                    lines.append(f"    - rect: {rect}, doors: {doors}")
            # Also include detected door tiles by wall type scan
            if wall_type_id:
                det = []
                for y in range(min(GRID_H, len(wall_type_id))):
                    row = wall_type_id[y]
                    if not row:
                        continue
                    for x in range(min(GRID_W, len(row))):
                        if row[x] == 'door1':
                            det.append([x, y])
                lines.append(f"  doors_detected: count={len(det)}")
                if det:
                    lines.append(f"  door_tiles: {det}")
        except Exception:
            pass
        # Player starts
        try:
            if player_state:
                lines.append('  players:')
                for sid, st in player_state.items():
                    cell = st.get('cell') if isinstance(st, dict) else None
                    if isinstance(cell, (list, tuple)) and len(cell) == 2:
                        cx, cy = int(cell[0]), int(cell[1])
                        lines.append(f"    - sid: {sid}, start_cell: [{cx}, {cy}]")
                    else:
                        lines.append(f"    - sid: {sid}, start_cell: null")
            else:
                lines.append('  players: []')
        except Exception:
            lines.append('  players: []')
        lines.append('')

        # Enemies
        lines.append('== ENEMIES ==')
        if not enemies:
            lines.append('(none)')
        else:
            for eid, e in enemies.items():
                etype = str(e.get('type') or '')
                tdef = types.get(etype) or {}
                name = str(tdef.get('name') or etype)
                pos = e.get('pos') or [0.0, 0.0]
                cx, cy = int(float(pos[0])), int(float(pos[1]))
                lines.append(f"- id: {eid}")
                lines.append(f"  type: {etype}")
                lines.append(f"  name: {name}")
                lines.append(f"  cell: [{cx}, {cy}]")
                # Affinities
                affin = fmt_aff(e.get('affinities') or {})
                lines.append(f"  affinities: {affin}")
                # Items held (if any fields exist)
                held: List[str] = []
                inv = e.get('inventory') or []
                if isinstance(inv, list):
                    for iid in inv:
                        if isinstance(iid, str):
                            nm = (ITEM_DB.get(iid) or {}).get('name') or iid
                            held.append(str(nm))
                lines.append(f"  holding: {held if held else '[]'}")
                # Descriptions
                descs = fill_desc(e)
                lines.append("  descriptions:")
                lines.append(f"    core: {descs['description_core']}")
                if descs['description_seeks']:
                    lines.append(f"    seeks: {descs['description_seeks']}")
                if descs['description_fears']:
                    lines.append(f"    fears: {descs['description_fears']}")
                if descs['description_vulnerable']:
                    lines.append(f"    vulnerable: {descs['description_vulnerable']}")
                if descs['backstory']:
                    lines.append(f"    backstory: {descs['backstory']}")
        lines.append('')

        # Classify entities for sections
        chest_lines: List[str] = []
        other_cont_lines: List[str] = []
        ground_item_lines: List[str] = []
        for ent in world_entities:
            try:
                if (ent.get('type') or 'item') != 'item':
                    continue
                iid = str(ent.get('item_id') or '')
                if not iid:
                    continue
                itdef = ITEM_DB.get(iid) or {}
                nm = str(itdef.get('name') or iid)
                is_container = bool(itdef.get('container'))
                spr = (ent.get('sprite') or {}).get('image') or ''
                is_chest = iid.startswith('chest_') or (iid == 'chest_basic') or ('chest' in nm.lower()) or ('chest' in str(spr).lower())
                pos = ent.get('pos') or [0.0, 0.0]
                cx, cy = int(float(pos[0])), int(float(pos[1]))
                if is_container and is_chest:
                    chest_lines.append(f"- chest: {iid} ({nm}) at [{cx}, {cy}]")
                    cont = ent.get('contents') or []
                    if not cont:
                        chest_lines.append("  contents: []")
                    else:
                        chest_lines.append("  contents:")
                        for entry in cont:
                            item_id = str((entry or {}).get('item') or '')
                            qty = int((entry or {}).get('qty') or 1)
                            it = ITEM_DB.get(item_id) or {}
                            iname = str(it.get('name') or item_id)
                            chest_lines.append(f"    - id: {item_id} x{qty} ({iname})")
                            # If this is a generated scroll, include visible text and icon
                            if item_id.startswith('scroll_'):
                                icon = str(it.get('icon') or '')
                                if icon:
                                    chest_lines.append(f"      icon: {icon}")
                                desc = str(it.get('description_core') or '')
                                if desc:
                                    for ln in desc.splitlines():
                                        chest_lines.append(f"      | {ln}")
                elif is_container:
                    other_cont_lines.append(f"- container: {iid} ({nm}) at [{cx}, {cy}]")
                    cont = ent.get('contents') or []
                    if not cont:
                        other_cont_lines.append("  contents: []")
                    else:
                        other_cont_lines.append("  contents:")
                        for entry in cont:
                            item_id = str((entry or {}).get('item') or '')
                            qty = int((entry or {}).get('qty') or 1)
                            it = ITEM_DB.get(item_id) or {}
                            iname = str(it.get('name') or item_id)
                            other_cont_lines.append(f"    - id: {item_id} x{qty} ({iname})")
                else:
                    # Ground items (equippable/leaves on floor)
                    if itdef.get('allowed_slots'):
                        ground_item_lines.append(f"- item: {iid} ({nm}) at [{cx}, {cy}]")
            except Exception:
                continue

        # Chests
        lines.append('== CHESTS ==')
        if chest_lines:
            lines.extend(chest_lines)
        else:
            lines.append('(none)')
        lines.append('')

        # Non-chest containers
        lines.append('== CONTAINERS (NON-CHEST) ==')
        if other_cont_lines:
            lines.extend(other_cont_lines)
        else:
            lines.append('(none)')
        lines.append('')

        # Ground items
        lines.append('== ITEMS (GROUND) ==')
        if ground_item_lines:
            lines.extend(ground_item_lines)
        else:
            lines.append('(none)')

        # Write file
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        WORLD_LOG_WRITTEN = True
    except Exception:
        # Do not crash game if logging fails
        WORLD_LOG_WRITTEN = True

def carve_rect(g, x0, y0, x1, y1, val=EMPTY):
    # inclusive rect bounds
    for y in range(max(0, y0), min(GRID_H, y1 + 1)):
        row = g[y]
        for x in range(max(0, x0), min(GRID_W, x1 + 1)):
            row[x] = val


def add_rooms(g, room_count: int, size: int = 9) -> List[Tuple[int, int]]:
    """Carve rectangular rooms onto grid g.
    - Rooms are `size` x `size` (default 9x9)
    - Interior (size-2) x (size-2) is EMPTY, leaving a 1-tile wall ring
    - Place 1-4 doors at midpoints of edges; a door is a WALL tile that will be
      assigned wall type 'door1' later. Carve 3-tile tunnel outward from each door
      to ensure connectivity into the maze corridors.
    Returns list of door coordinates [(x,y), ...] for later type/HP assignment.
    """
    global ROOMS
    doors: List[Tuple[int, int]] = []
    if room_count <= 0 or size < 5:
        return doors
    ring = 1
    inner = size - 2
    # Require margin from outer border so tunnels can be carved (3 outward)
    tunnel_len = 3
    margin = ring + tunnel_len + 1  # +1 to avoid outer wall at index 0/GRID-1
    attempts = max(50, room_count * 20)
    placed = 0
    for _ in range(attempts):
        if placed >= room_count:
            break
        x0 = random.randrange(margin, GRID_W - margin - size + 1)
        y0 = random.randrange(margin, GRID_H - margin - size + 1)
        x1 = x0 + size - 1
        y1 = y0 + size - 1
        # Previously required the area to be all walls. Relax this so rooms can
        # overwrite corridors; tunnels will reconnect rooms to the maze.
        # Fill full area with walls (ensures ring), then carve interior EMPTY
        carve_rect(g, x0, y0, x1, y1, WALL)
        carve_rect(g, x0 + ring, y0 + ring, x1 - ring, y1 - ring, EMPTY)
        # Door candidates: midpoints of each side on the ring
        mids = [
            (x0 + size // 2, y0),        # top
            (x0 + size // 2, y1),        # bottom
            (x0, y0 + size // 2),        # left
            (x1, y0 + size // 2),        # right
        ]
        # Randomly choose 1-4 doors
        k = random.randint(1, 4)
        random.shuffle(mids)
        picks = mids[:k]
        room_doors: List[Tuple[int, int]] = []
        for (dx, dy) in picks:
            # Ensure ring at door position is a wall
            if g[dy][dx] != WALL:
                g[dy][dx] = WALL
            doors.append((dx, dy))
            room_doors.append((dx, dy))
            # Carve tunnel outward from the door (not through the door tile itself)
            if dy == y0 and dx != x0 and dx != x1:
                # top edge; outward is -y
                ox, oy = dx, dy - 1
                for t in range(tunnel_len):
                    ty = oy - t
                    if 1 <= ty < GRID_H - 1:
                        g[ty][ox] = EMPTY
            elif dy == y1 and dx != x0 and dx != x1:
                # bottom edge; outward is +y
                ox, oy = dx, dy + 1
                for t in range(tunnel_len):
                    ty = oy + t
                    if 1 <= ty < GRID_H - 1:
                        g[ty][ox] = EMPTY
            elif dx == x0 and dy != y0 and dy != y1:
                # left edge; outward is -x
                ox, oy = dx - 1, dy
                for t in range(tunnel_len):
                    tx = ox - t
                    if 1 <= tx < GRID_W - 1:
                        g[oy][tx] = EMPTY
            elif dx == x1 and dy != y0 and dy != y1:
                # right edge; outward is +x
                ox, oy = dx + 1, dy
                for t in range(tunnel_len):
                    tx = ox + t
                    if 1 <= tx < GRID_W - 1:
                        g[oy][tx] = EMPTY
        # Persist room metadata for logs
        ROOMS.append({
            'rect': [x0, y0, x1, y1],
            'doors': [[dx, dy] for (dx, dy) in room_doors],
        })
        placed += 1
    return doors


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
        ent = {
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
        }
        _attach_container_contents(ent)
        world_entities.append(ent)
        rebuild_solid_cells()
        return


def place_pillar_next_to(cx: int, cy: int, welcome: bool = False):
    """Try to place a knowledge pillar on an adjacent empty tile to (cx, cy) for QA.
    Prefers to attach one pending scroll from SCROLL_QUEUE if available.
    Ensures enemies and pillars are initialized so scrolls exist.
    """
    # Make sure world/entities/enemies exist so we can generate pillars/scrolls if needed
    try:
        init_entities_once()
    except Exception:
        pass
    try:
        init_random_enemies_once()
    except Exception:
        pass
    # Generate scrolls and pillars once so SCROLL_QUEUE is populated
    try:
        ensure_scrolls_generated_once()
    except Exception:
        pass
    try:
        ensure_knowledge_pillars_once()
    except Exception:
        pass

    # Choose adjacent cell
    for dx, dy in ((1,0), (-1,0), (0,1), (0,-1)):
        nx, ny = cx + dx, cy + dy
        if not (0 <= nx < GRID_W and 0 <= ny < GRID_H):
            continue
        if grid[ny][nx] != EMPTY:
            continue
        if (nx, ny) in occupied or (nx, ny) in solid_cells:
            continue
        # Avoid overlapping existing entity at same integer cell
        conflict = False
        for ent in world_entities:
            pos = ent.get('pos') or ent.get('position')
            if not pos or len(pos) < 2:
                continue
            ex, ey = int(float(pos[0])), int(float(pos[1]))
            if ex == nx and ey == ny:
                conflict = True
                break
        if conflict:
            continue
        # Determine a pillar type and optional scroll content
        pillar_type = _pillar_type_for_element('')
        contents = []
        if welcome:
            # Ensure a custom welcome scroll exists and attach it
            try:
                if not ITEM_DB.get('scroll_welcome'):
                    register_item({
                        'id': 'scroll_welcome',
                        'name': 'Welcome Scroll',
                        'active': True,
                        'allowed_slots': [],
                        'spawn_type': None,
                        'icon': (ITEM_DB.get('scroll_of_knowledge') or {}).get('icon') or 'items/scroll_of_knowledge.png',
                        'special': True,
                        'stats': { 'weight': 0.2, 'durability': 1 },
                        'ranged_attack': 0,
                    })
                contents.append({'item': 'scroll_welcome', 'qty': 1})
            except Exception:
                pass
        elif SCROLL_QUEUE:
            try:
                sid = SCROLL_QUEUE.pop(0)
                contents.append({'item': sid, 'qty': 1})
            except Exception:
                pass
        ent = {
            'type': 'item',
            'item_id': pillar_type,
            'pos': [float(nx) + 0.5, float(ny) + 0.5],
            'container': True,
            'contents': contents,
            'sprite': {
                'image': 'items/pillar_of_knowledge.png',
                'base_width': 64,
                'base_height': 128,
                'scale': 1.0,
                'y_offset': 0,
            }
        }
        world_entities.append(ent)
        rebuild_solid_cells()
        # Mark one-time start pillar placement if this was the welcome pillar
        if welcome:
            global START_PILLAR_PLACED
            START_PILLAR_PLACED = True
        return True
    return False


def maybe_spawn_test_items_near_start(cx: int, cy: int) -> None:
    """If QA test mode is enabled in config, spawn a configured item at fixed
    distances near the first player's start area, preferring positions next to a wall.
    Runs once per run.
    """
    global TEST_ITEMS_SPAWNED
    if TEST_ITEMS_SPAWNED:
        return
    try:
        cfg = game_config.get_game_config() or {}
        qa = (cfg.get('qa') or {}).get('test_item') or {}
        enabled = bool(qa.get('enabled', False))
        if not enabled:
            return
        item_id = str(qa.get('item_id') or 'pillar_of_knowledge')
        distances = qa.get('distances') or [1, 2, 3, 4, 5]
        if not isinstance(distances, list):
            distances = [1, 2, 3, 4, 5]
        spawn_next_to_wall = bool(qa.get('spawn_next_to_wall', True))
        per_dist = int(qa.get('spawn_count_per_dist', 1) or 1)
    except Exception:
        return

    def empty_and_valid(tx: int, ty: int) -> bool:
        if not (0 <= tx < GRID_W and 0 <= ty < GRID_H):
            return False
        if grid[ty][tx] != EMPTY:
            return False
        if (tx, ty) in occupied or (tx, ty) in solid_cells:
            return False
        for ent in world_entities:
            pos = ent.get('pos') or ent.get('position')
            if not pos or len(pos) < 2:
                continue
            ex, ey = int(float(pos[0])), int(float(pos[1]))
            if ex == tx and ey == ty:
                return False
        return True

    def adj_to_wall(tx: int, ty: int) -> bool:
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, ny = tx + dx, ty + dy
            if 0 <= nx < GRID_W and 0 <= ny < GRID_H and grid[ny][nx] == WALL:
                return True
        return False

    spawned_any = False
    # Prefer +x direction into the maze; fallback to other straight directions
    dirs = [(1,0), (-1,0), (0,1), (0,-1)]
    for d in distances:
        placed_for_d = 0
        for (dx, dy) in dirs:
            if placed_for_d >= per_dist:
                break
            tx, ty = cx + dx * int(d), cy + dy * int(d)
            if not empty_and_valid(tx, ty):
                continue
            if spawn_next_to_wall and not adj_to_wall(tx, ty):
                # Try to nudge along perpendicular by 1 to hug a wall if available
                nudges = [(0,1), (0,-1), (1,0), (-1,0)] if dx != 0 else [(1,0), (-1,0), (0,1), (0,-1)]
                found = False
                for ndx, ndy in nudges:
                    ntx, nty = tx + ndx, ty + ndy
                    if empty_and_valid(ntx, nty) and adj_to_wall(ntx, nty):
                        tx, ty = ntx, nty
                        found = True
                        break
                if not found:
                    # If we insist on wall-adjacent but none found nearby, allow original if ok
                    if not adj_to_wall(tx, ty):
                        continue
            # Spawn entity
            ent = {
                'type': 'item',
                'item_id': item_id,
                'pos': [float(tx) + 0.5, float(ty) + 0.5],
                'sprite': {
                    'image': f'items/{item_id}.png',
                    'base_width': 64,
                    'base_height': 64,
                    'scale': 1.0,
                    'y_offset': 0,
                }
            }
            world_entities.append(ent)
            placed_for_d += 1
            spawned_any = True
    if spawned_any:
        rebuild_solid_cells()
        TEST_ITEMS_SPAWNED = True


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
            # Deterministic spawn within 8x8 starting area based on sid hash
            sx0, sx1 = 1, 8
            sy0 = max(1, GRID_H // 2 - 4)
            sy1 = min(GRID_H - 2, sy0 + 7)
            candidates = [(x, y) for y in range(sy0, sy1 + 1) for x in range(sx0, sx1 + 1)]
            n = len(candidates)
            try:
                h = int.from_bytes(hashlib.sha256(sid.encode('utf-8')).digest()[:4], 'big')
            except Exception:
                h = abs(hash(sid))
            start_idx = h % max(1, n)
            chosen = None
            for k in range(n):
                cx_try, cy_try = candidates[(start_idx + k) % n]
                if grid[cy_try][cx_try] == EMPTY and (cx_try, cy_try) not in occupied and (cx_try, cy_try) not in solid_cells:
                    chosen = (cx_try, cy_try)
                    break
            if chosen is None:
                cx, cy = random_empty_cell()
            else:
                cx, cy = chosen
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
        # Chest spawning next to new spawns was for testing only and is now disabled
        init_entities_once()
        # For testing: place a single welcome pillar adjacent to the FIRST NEW spawn only
        if restore_cell is None and not START_PILLAR_PLACED:
            place_pillar_next_to(cx, cy, welcome=True)
            # Optionally spawn configured QA test items at fixed distances near start
            maybe_spawn_test_items_near_start(cx, cy)


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

    # Live tuning state (loaded from config, adjustable via UI)
    try:
        _cfg = game_config.get_game_config() or {}
        _tun = (_cfg.get('tuning') or {}) if isinstance(_cfg, dict) else {}
        item_floor_bias_px = int(_tun.get('item_floor_bias_px', 0) or 0)
        item_bias_step_px = max(1, int(_tun.get('item_bias_step_px', 1) or 1))
        # Live scale bias (multiplier) and step for tuning item size
        item_scale_bias_mult = float(_tun.get('item_scale_bias_mult', 1.0) or 1.0)
        item_scale_step = float(_tun.get('item_scale_step', 0.05) or 0.05)
    except Exception:
        item_floor_bias_px = 0
        item_bias_step_px = 1
        item_scale_bias_mult = 1.0
        item_scale_step = 0.05

    # UI button rects for per-player controls (rebuilt each frame)
    ui_bias_buttons = {}  # sid -> { 'up': Rect, 'down': Rect }
    ui_scale_buttons = {}  # sid -> { 'up': Rect, 'down': Rect }

    # Ensure world is initialized before loop
    init_grid_once()
    init_entities_once()
    init_random_enemies_once()
    # After enemies exist, ensure knowledge pillars are spawned once (idempotent)
    ensure_knowledge_pillars_once()
    ensure_scrolls_generated_once()
    write_world_log_once()

    while running:
        # Events to allow clean quit
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                try:
                    mx, my = event.pos
                    # Only consider clicks within sidebar area
                    if 0 <= mx < SIDEBAR_WIDTH:
                        for sid, btns in list(ui_bias_buttons.items()):
                            up_r = btns.get('up')
                            dn_r = btns.get('down')
                            if up_r and up_r.collidepoint(mx, my):
                                item_floor_bias_px += item_bias_step_px
                                break
                            if dn_r and dn_r.collidepoint(mx, my):
                                item_floor_bias_px -= item_bias_step_px
                                break
                        # Scale bias buttons
                        for sid, btns in list(ui_scale_buttons.items()):
                            up_r = btns.get('up')
                            dn_r = btns.get('down')
                            if up_r and up_r.collidepoint(mx, my):
                                item_scale_bias_mult = min(3.0, item_scale_bias_mult + item_scale_step)
                                break
                            if dn_r and dn_r.collidepoint(mx, my):
                                item_scale_bias_mult = max(0.1, item_scale_bias_mult - item_scale_step)
                                break
                except Exception:
                    pass

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
            0: (135, 206, 235),  # sky blue for non-biome
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
                                base = biome_colors.get(bid, (135, 206, 235))
                                cr += base[0] * w
                                cg += base[1] * w
                                cb += base[2] * w
                                wt_sum += w
                        if wt_sum > 0:
                            col = (int(cr / wt_sum), int(cg / wt_sum), int(cb / wt_sum))
                        else:
                            bid = biomes[y][x] if biomes else 0
                            col = biome_colors.get(bid, (135, 206, 235))
                    else:
                        bid = biomes[y][x] if biomes else 0
                        col = biome_colors.get(bid, (135, 206, 235))
                    pygame.draw.rect(screen, col, (tx, ty, TILE_SIZE, TILE_SIZE))
        # Draw walls on top; doors (door1) are brown, others white
        wall_color = (255, 255, 255)
        for y in range(GRID_H):
            for x in range(GRID_W):
                if grid[y][x] == WALL:
                    tx, ty = cell_to_px(x, y)
                    if visible_mask[y][x]:
                        col = wall_color
                        try:
                            if wall_type_id and wall_type_id[y][x] == 'door1':
                                col = (150, 90, 40)  # brown for wooden door
                        except Exception:
                            pass
                        pygame.draw.rect(screen, col, (tx, ty, TILE_SIZE, TILE_SIZE))
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
                # Determine equipped instance in that hand
                pdata_srv = players.get(sid, {})
                eq = (pdata_srv.get('equipment') or {})
                items_map = (pdata_srv.get('items') or {})
                inst_id = eq.get(f'{act}_hand')
                inst = (items_map.get(inst_id) or {}) if inst_id else {}
                type_id = (inst.get('type') or '') if inst else ''
                it = ITEM_DB.get(type_id or '') or {}
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
                            # Check wall type damage gating
                            allow = True
                            try:
                                wt = wall_type_id[ty][tx] if wall_type_id else ''
                                wt_info = get_wall_type_map().get(wt) or {}
                                dmg_list = wt_info.get('damage_items')
                                if isinstance(dmg_list, list):
                                    allow = (type_id in dmg_list)
                            except Exception:
                                allow = True
                            if not allow:
                                # Not effective on this wall type
                                did_hit = False
                            else:
                                # apply damage to wall hp using wall type durability
                                try:
                                    wt = wall_type_id[ty][tx] if wall_type_id else ''
                                    wt_info = get_wall_type_map().get(wt) or {}
                                    wt_stats = (wt_info.get('stats') or {})
                                except Exception:
                                    wt_stats = {}
                                max_loc = max(1, int((wt_stats.get('durability', 1) or 1)))
                                if wall_hp[ty][tx] <= 0:
                                    wall_hp[ty][tx] = max_loc
                                wall_hp[ty][tx] = max(0, wall_hp[ty][tx] - wall_damage)
                                if wall_hp[ty][tx] <= 0:
                                    grid[ty][tx] = EMPTY
                                    wall_hp[ty][tx] = 0
                                # tool durability loss: wall returns damage to the specific instance
                                try:
                                    # Ensure instance has durability field initialized
                                    if inst_id and inst is not None and ('durability' not in inst):
                                        base_dur = int((it.get('stats') or {}).get('durability', 0) or 0)
                                        inst['durability'] = base_dur
                                        items_map[inst_id] = inst
                                    # wall deals its damage to the tool instance
                                    # walls.json uses 'damaged' (how much they inflict back); fall back to 'damage'
                                    td = wt_stats.get('damage')
                                    if td is None:
                                        td = wt_stats.get('damaged')
                                    tool_damage = int(td or 0)
                                    if tool_damage > 0 and inst_id:
                                        cur = int((inst or {}).get('durability') or 0)
                                        new_dur = max(0, cur - tool_damage)
                                        inst['durability'] = new_dur
                                        items_map[inst_id] = inst
                                        if new_dur <= 0:
                                            # break the tool: unequip and remove instance from inventory/map
                                            slot_key = f'{act}_hand'
                                            if players[sid]['equipment'].get(slot_key) == inst_id:
                                                players[sid]['equipment'][slot_key] = None
                                            # Remove instance from inventory if present
                                            try:
                                                inv = players[sid].get('inventory') or []
                                                if inst_id in inv:
                                                    inv.remove(inst_id)
                                            except Exception:
                                                pass
                                            # Remove the instance record
                                            (players.get(sid, {}).get('items') or {}).pop(inst_id, None)
                                            # emit equipment snapshot (include durability info for HUD)
                                            try:
                                                def _equip_payload(pmap):
                                                    out_types = {}
                                                    out_insts = {}
                                                    # rich objects per slot for durability-aware HUD
                                                    rich = {}
                                                    for k, iid in (pmap.get('equipment') or {}).items():
                                                        out_insts[k] = iid or None
                                                        if iid:
                                                            ii = (pmap.get('items') or {}).get(iid) or {}
                                                            t_id = ii.get('type')
                                                            itdef = ITEM_DB.get(t_id or '') or {}
                                                            stats = itdef.get('stats') or {}
                                                            out_types[k] = t_id
                                                            rich[k] = {
                                                                'id': t_id,
                                                                'name': itdef.get('name'),
                                                                'durability': int(ii.get('durability') or 0),
                                                                'max_durability': int(stats.get('durability') or 0),
                                                            }
                                                        else:
                                                            out_types[k] = None
                                                            rich[k] = None
                                                    return out_types, out_insts, rich
                                                eq_types, eq_insts, eq_rich = _equip_payload(players[sid])
                                                socketio.emit('equip', {'equipment': eq_rich, 'equipment_instances': eq_insts}, to=sid)
                                            except Exception:
                                                pass
                                        else:
                                            # tool damaged but not broken -> emit updated equip snapshot for live HUD update
                                            try:
                                                def _equip_payload(pmap):
                                                    out_insts = {}
                                                    rich = {}
                                                    for k, iid in (pmap.get('equipment') or {}).items():
                                                        out_insts[k] = iid or None
                                                        if iid:
                                                            ii = (pmap.get('items') or {}).get(iid) or {}
                                                            t_id = ii.get('type')
                                                            itdef = ITEM_DB.get(t_id or '') or {}
                                                            stats = itdef.get('stats') or {}
                                                            rich[k] = {
                                                                'id': t_id,
                                                                'name': itdef.get('name'),
                                                                'durability': int(ii.get('durability') or 0),
                                                                'max_durability': int(stats.get('durability') or 0),
                                                            }
                                                        else:
                                                            rich[k] = None
                                                    return out_insts, rich
                                                eq_insts, eq_rich = _equip_payload(players[sid])
                                                socketio.emit('equip', {'equipment': eq_rich, 'equipment_instances': eq_insts}, to=sid)
                                            except Exception:
                                                pass
                                except Exception:
                                    # Ignore durability update errors to avoid breaking gameplay
                                    pass
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
                else:
                    # No wall-damage tool: treat as interaction with the front tile (e.g., read pillar scroll)
                    try:
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
                        # Find a pillar entity at target cell
                        pillar = None
                        for ent in world_entities:
                            try:
                                if (ent.get('type') or 'item') != 'item':
                                    continue
                                item_id = str(ent.get('item_id') or '')
                                if not item_id.startswith('pillar_of_knowledge'):
                                    continue
                                pos = ent.get('pos') or ent.get('position')
                                if not pos or len(pos) < 2:
                                    continue
                                ex, ey = int(float(pos[0])), int(float(pos[1]))
                                if ex == tx and ey == ty:
                                    pillar = ent
                                    break
                            except Exception:
                                continue
                        if pillar and isinstance(pillar.get('contents'), list) and pillar['contents']:
                            # Read the first scroll entry
                            entry = pillar['contents'][0]
                            scroll_id = str(entry.get('item') or '')
                            if scroll_id:
                                lore = _scroll_lore_text(scroll_id)
                                # Mark as read
                                try:
                                    mark_scroll_read(sid, scroll_id)
                                except Exception:
                                    pass
                                # Emit overlay to this player
                                try:
                                    socketio.emit('scroll_overlay', {
                                        'text': lore or 'You read the ancient script...'
                                    }, to=sid)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    # Per-hit degradation is handled by wall return damage above (instance-based).

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

            # sidebar listing (name)
            name = pdata.get('name', 'Player')
            screen.blit(font.render(name, True, (200, 200, 200)), (20, list_y))
            list_y += 22
            # live bias + distance display and buttons per player
            try:
                # Find nearest pillar distance (in tiles)
                cx, cy = player_state[sid]['cell']
                nearest_dist = None
                for ent in world_entities:
                    try:
                        if (ent.get('type') or 'item') != 'item':
                            continue
                        item_id = str(ent.get('item_id') or '')
                        if not item_id.startswith('pillar_of_knowledge'):
                            continue
                        pos = ent.get('pos') or ent.get('position')
                        if not pos or len(pos) < 2:
                            continue
                        ex, ey = float(pos[0]), float(pos[1])
                        d = math.hypot(ex - float(cx), ey - float(cy))
                        if (nearest_dist is None) or (d < nearest_dist):
                            nearest_dist = d
                    except Exception:
                        continue
                dist_txt = f"{nearest_dist:.1f}" if nearest_dist is not None else "-"
            except Exception:
                dist_txt = "-"

            # Text line: bias and distance (keep concise)
            info_text = f"bias: {int(item_floor_bias_px)}  dist: {dist_txt}"
            screen.blit(font.render(info_text, True, (160, 160, 160)), (20, list_y))

            # Up/Down small buttons for Y-bias, anchored within sidebar
            BTN_W, SP, RIGHT_M = 22, 6, 14
            btns_total = BTN_W * 2 + SP
            btn_x0 = SIDEBAR_WIDTH - RIGHT_M - btns_total  # ensure fully inside 0..SIDEBAR_WIDTH
            up_rect = pygame.Rect(btn_x0, list_y - 2, BTN_W, BTN_W)
            dn_rect = pygame.Rect(btn_x0 + BTN_W + SP, list_y - 2, BTN_W, BTN_W)
            pygame.draw.rect(screen, (60,60,60), up_rect)
            pygame.draw.rect(screen, (60,60,60), dn_rect)
            screen.blit(font.render('▲', True, (220, 220, 220)), (up_rect.x + 5, up_rect.y))
            screen.blit(font.render('▼', True, (220, 220, 220)), (dn_rect.x + 5, dn_rect.y))
            # Register rects for click detection
            ui_bias_buttons[sid] = { 'up': up_rect, 'down': dn_rect }

            # Up/Down small buttons for Scale bias (next row), also fully inside
            list_y += 20
            scale_lbl = f"Scale: {item_scale_bias_mult:.2f}"
            screen.blit(font.render(scale_lbl, True, (140, 140, 140)), (20, list_y))
            sup_rect = pygame.Rect(btn_x0, list_y - 2, BTN_W, BTN_W)
            sdn_rect = pygame.Rect(btn_x0 + BTN_W + SP, list_y - 2, BTN_W, BTN_W)
            pygame.draw.rect(screen, (60,60,60), sup_rect)
            pygame.draw.rect(screen, (60,60,60), sdn_rect)
            screen.blit(font.render('▲', True, (220, 220, 220)), (sup_rect.x + 5, sup_rect.y))
            screen.blit(font.render('▼', True, (220, 220, 220)), (sdn_rect.x + 5, sdn_rect.y))
            ui_scale_buttons[sid] = { 'up': sup_rect, 'down': sdn_rect }

            list_y += 28

        

        # Draw entity markers after biome overlays.
        # Chests render as yellow dots for visibility; pillars render as black dots; others remain green.
        # If visibility.show_chests is true, chests are shown even through fog.
        for ent in world_entities:
            pos = ent.get('pos') or ent.get('position')
            if not pos or len(pos) < 2:
                continue
            ex, ey = float(pos[0]), float(pos[1])
            # Hide entities in unseen tiles when fog is enabled
            ix, iy = int(ex), int(ey)
            # Determine marker color and chest type
            item_id = str(ent.get('item_id') or '')
            is_chest = item_id.startswith('chest_')
            is_pillar = item_id.startswith('pillar_of_knowledge')
            # Config toggle: show chests through fog
            try:
                show_chests_through_fog = bool((vis_cfg or {}).get('show_chests', True))
            except Exception:
                show_chests_through_fog = True
            # Visibility gate: allow chests if configured, otherwise require visibility
            tile_visible = (0 <= iy < GRID_H and 0 <= ix < GRID_W and visible_mask[iy][ix])
            if not tile_visible and not (is_chest and show_chests_through_fog):
                continue
            if is_pillar:
                dot_color = (0, 0, 0)
            else:
                dot_color = (240, 220, 0) if is_chest else (50, 220, 50)
            # convert grid coords (floats) to pixel space
            px = BOARD_ORIGIN_X + int(ex * TILE_SIZE)
            py = BOARD_ORIGIN_Y + int(ey * TILE_SIZE)
            # 2x2 dot centered-ish
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
                # Distance-based tuning: per-item overrides or global defaults
                try:
                    tuning = (game_config.get_game_config() or {}).get('tuning') or {}
                except Exception:
                    tuning = {}
                # Resolve per-item render curves via item definition if available
                item_id = str(ent.get('item_id') or '')
                itdef = ITEM_DB.get(item_id) or {}
                rblock = (itdef.get('render') or {}) if isinstance(itdef.get('render'), dict) else {}
                scale_curve = rblock.get('scale_curve') or tuning.get('item_scale_curve_default') or []
                y_curve = rblock.get('y_bias_curve') or tuning.get('item_y_bias_curve_default') or []
                # Sample curves at current distance
                scale_mult_curve = _sample_curve(scale_curve, dist, default=1.0)
                y_bias_curve = _sample_curve(y_curve, dist, default=0.0)

                item_scale_mult = 0.5 if type_str == 'item' else 1.0
                # Apply live scale bias multiplier for items
                out_h = int(base * (base_h / 64.0) * scale * item_scale_mult * float(scale_mult_curve) * float(item_scale_bias_mult))
                out_h = max(1, min(3 * RC_H, out_h))
                aspect = base_w / max(1, base_h)
                out_w = int(out_h * aspect)
                center_x = ray_x
                x = center_x - out_w // 2
                y = (RC_H - out_h) // 2 - y_off - int(y_bias_curve)
                # Anchor items to an empirical floor line that shifts with distance.
                # This better matches the floor perspective in our renderer.
                if type_str == 'item':
                    # Base floor line around ~86% of the screen height (lower on screen = larger Y)
                    # Gentle taper with distance to keep items seated when far
                    # Close (dist~1): ~0.86H - 1px; Far (dist>=12): ~0.86H - 6px
                    floor_y = int((RC_H * 86) // 100)
                    floor_y -= int(min(6, 0.5 * max(0.0, dist)))
                    # Apply live bias from tuning UI (positive lowers the sprite)
                    floor_y += int(item_floor_bias_px)
                    # Place item so its bottom sits on this floor line
                    # Add distance bias from curve as well
                    y = floor_y - out_h - y_off - int(y_bias_curve)

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
                # Use the actual PNG intrinsic size so slicing uses the full sprite
                base_w, base_h = _get_image_natural_size(image)
                scale = 1.0
                y_off = 0

                # Distance-based tuning for enemies
                try:
                    tuning = (game_config.get_game_config() or {}).get('tuning') or {}
                except Exception:
                    tuning = {}
                rblock = (info.get('render') or {}) if isinstance(info.get('render'), dict) else {}
                e_scale_curve = rblock.get('scale_curve') or tuning.get('enemy_scale_curve_default') or []
                e_y_curve = rblock.get('y_bias_curve') or tuning.get('enemy_y_bias_curve_default') or []
                e_scale_mult = _sample_curve(e_scale_curve, dist, default=1.0)
                e_y_bias = _sample_curve(e_y_curve, dist, default=0.0)

                base = RC_H / max(1e-3, dist)
                out_h = int(base * (base_h / 64.0) * scale * float(e_scale_mult))
                out_h = max(1, min(3 * RC_H, out_h))
                aspect = base_w / max(1, base_h)
                out_w = int(out_h * aspect)
                center_x = ray_x
                x = center_x - out_w // 2
                y = (RC_H - out_h) // 2 - y_off - int(e_y_bias)

                sprites.append({
                    'img': f'items/{image}', 'sx': 0, 'sy': 0, 'sw': base_w, 'sh': base_h,
                    'x': x, 'y': y, 'w': out_w, 'h': out_h, 'depth': dist
                })

            # Add other players as billboard sprites (use recolored sprite if available)
            for other_sid, op in list(players.items()):
                if other_sid == sid:
                    continue  # don't render the viewing player as a sprite
                ost = player_state.get(other_sid)
                if not ost:
                    continue
                ocx, ocy = ost.get('cell', (None, None))
                if ocx is None or ocy is None:
                    continue
                # Player center in cell space
                ex = float(ocx) + 0.5
                ey = float(ocy) + 0.5
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
                norm = (rel + RC_FOV/2) / RC_FOV
                ray_x = int(norm * (RC_NUM_RAYS - 1))
                ray_x = max(0, min(RC_NUM_RAYS - 1, ray_x))

                # Resolve sprite path: recolored if available, else base character
                img_path = None
                sp = (op or {}).get('sprite_path')
                if isinstance(sp, str) and sp:
                    # convert '/static/img/...' to relative '...'
                    prefix = '/static/img/'
                    img_path = sp[len(prefix):] if sp.startswith(prefix) else sp.lstrip('/')
                if not img_path:
                    ch = (op or {}).get('character') or 'girl_elf'
                    img_path = f"players/{ch}.png"

                # Source sprite nominal size (PNG portrait 128x256); normalize scale
                base_w = 128
                base_h = 256
                # 0.25 brings 256-high sources down to match 64-high baseline scaling
                scale = 0.25
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
                    'img': img_path, 'sx': 0, 'sy': 0, 'sw': base_w, 'sh': base_h,
                    'x': x, 'y': y, 'w': out_w, 'h': out_h, 'depth': dist,
                    'kind': 'player'
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
