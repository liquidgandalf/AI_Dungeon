# AI_Dungeon/app/enemy_ai.py
from __future__ import annotations
from typing import Dict, List, Literal, Optional, Tuple, TypedDict
import math
import random

# Intent models
IntentKind = Literal['move', 'attack', 'idle']

class MoveIntent(TypedDict):
    kind: Literal['move']
    id: str
    dx: int
    dy: int

class AttackIntent(TypedDict):
    kind: Literal['attack']
    id: str
    target_id: str

class IdleIntent(TypedDict):
    kind: Literal['idle']
    id: str

Intent = MoveIntent | AttackIntent | IdleIntent

# Enemy instance model expected by AI
class EnemyInst(TypedDict, total=False):
    id: str
    type: str
    pos: Tuple[int, int]
    hp: int
    biome: Optional[int]
    spawner: Optional[Tuple[int, int]]
    state: Dict[str, float]

# World view provided to AI
class WorldView(TypedDict):
    grid_w: int
    grid_h: int
    walls: List[List[int]]  # same grid as game.py (0 empty, 1 wall)
    occupied: Dict[Tuple[int, int], str]  # players only
    solid_cells: set[Tuple[int, int]]
    players: Dict[str, Tuple[int, int]]  # sid -> (cx, cy)


def _nearest_player(pos: Tuple[int, int], players: Dict[str, Tuple[int, int]]) -> Optional[Tuple[str, Tuple[int, int], float]]:
    if not players:
        return None
    x, y = pos
    best = None
    best_d2 = 10**9
    for sid, (px, py) in players.items():
        dx = px - x
        dy = py - y
        d2 = dx*dx + dy*dy
        if d2 < best_d2:
            best_d2 = d2
            best = (sid, (px, py), math.sqrt(d2))
    return best


def slime_ai(enemy: EnemyInst, world: WorldView) -> Intent:
    # Simple AI: if player within 8 tiles (euclidean), step towards; else random wander with cooldown
    pos = enemy.get('pos', (0, 0))
    state = enemy.setdefault('state', {})
    roam_cd = state.get('roam_cd', 0.0)

    target_info = _nearest_player(pos, world['players'])
    if target_info is not None:
        sid, (tx, ty), dist = target_info
        if dist <= 1.0:
            return {'kind': 'attack', 'id': enemy['id'], 'target_id': sid}
        if dist <= 8.0:
            dx = 1 if tx > pos[0] else (-1 if tx < pos[0] else 0)
            dy = 1 if ty > pos[1] else (-1 if ty < pos[1] else 0)
            # Prefer axis with larger gap to avoid diagonal bias
            if abs(tx - pos[0]) >= abs(ty - pos[1]):
                return {'kind': 'move', 'id': enemy['id'], 'dx': dx, 'dy': 0}
            else:
                return {'kind': 'move', 'id': enemy['id'], 'dx': 0, 'dy': dy}

    # Wander occasionally
    if roam_cd <= 0:
        state['roam_cd'] = random.uniform(0.5, 2.0)
        choice = random.choice([(1,0), (-1,0), (0,1), (0,-1), (0,0)])
        if choice != (0,0):
            return {'kind': 'move', 'id': enemy['id'], 'dx': choice[0], 'dy': choice[1]}
    else:
        state['roam_cd'] = max(0.0, roam_cd - 0.1)  # caller should call at ~10 fps for this to feel okay
    return {'kind': 'idle', 'id': enemy['id']}


AI_DISPATCH = {
    'slime_green': slime_ai,
    'slime_blue': slime_ai,
    'slime_red': slime_ai,
}


def compute_enemy_intents(world: WorldView, enemies: Dict[str, EnemyInst]) -> List[Intent]:
    intents: List[Intent] = []
    for eid, enemy in enemies.items():
        t = enemy.get('type', '')
        fn = AI_DISPATCH.get(t, slime_ai)
        try:
            intent = fn(enemy, world)
        except Exception:
            intent = {'kind': 'idle', 'id': enemy['id']}
        intents.append(intent)
    return intents
