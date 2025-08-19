# AI_Dungeon/app/config.py
import json
import os
from typing import Any, Dict, List

_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_config_dir = os.path.join(_base_dir, 'config')

_game_config: Dict[str, Any] = {}
_items: List[Dict[str, Any]] = []
_wall_types: List[Dict[str, Any]] = []
_enemy_types: List[Dict[str, Any]] = []
_map_entities: List[Dict[str, Any]] = []


def _load_json(path: str, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def reload_all() -> None:
    global _game_config, _items, _wall_types, _enemy_types, _map_entities
    os.makedirs(_config_dir, exist_ok=True)
    _game_config = _load_json(os.path.join(_config_dir, 'game_config.json'), {})
    _items = _load_json(os.path.join(_config_dir, 'items.json'), [])
    _wall_types = _load_json(os.path.join(_config_dir, 'wall_types.json'), [])
    _enemy_types = _load_json(os.path.join(_config_dir, 'enemy_types.json'), [])
    _map_entities = _load_json(os.path.join(_config_dir, 'map_entities.json'), [])


def get_game_config() -> Dict[str, Any]:
    if not _game_config:
        reload_all()
        # defaults if missing keys
    # sensible defaults
    _game_config.setdefault('seed', None)
    _game_config.setdefault('initial_attributes_count', 10)
    _game_config.setdefault('speed', {
        'maxspeedpermove': 1,
        'minspeed': 3,
        'max_speed_stat': 16,
        'min_speed_stat': 1,
    })
    _game_config.setdefault('spawns', {
        'random_items': 0,
        'random_chests': 0,
        'random_enemies': 0,
    })
    # Ensure keys exist if file provided a partial 'spawns'
    try:
        sp = _game_config.get('spawns') or {}
        sp.setdefault('random_items', 0)
        sp.setdefault('random_chests', 0)
        sp.setdefault('random_enemies', 0)
        _game_config['spawns'] = sp
    except Exception:
        pass
    _game_config.setdefault('biomes', {
        'count': 6,
        'radius': 24,
    })
    # Enemy system defaults
    enemies_cfg = _game_config.get('enemies') or {}
    if not isinstance(enemies_cfg, dict):
        enemies_cfg = {}
    enemies_cfg.setdefault('move', True)
    _game_config['enemies'] = enemies_cfg
    # Visibility/fog-of-war defaults
    vis = _game_config.get('visibility') or {}
    if not isinstance(vis, dict):
        vis = {}
    vis.setdefault('mode', 'reveal')  # 'full', 'fog', or 'reveal'
    vis.setdefault('reveal_radius', 6)  # in tiles
    # Big-map visibility toggles
    vis.setdefault('enemies', True)       # show enemy markers on big map
    vis.setdefault('enemy_pings', True)   # show radar pings on big map
    vis.setdefault('enemy_pings_ignore_visibility', True)  # show pings even on unseen tiles
    # Show chest markers through fog on big map
    vis.setdefault('show_chests', True)
    _game_config['visibility'] = vis
    # Chest loot tables (optional)
    _game_config.setdefault('chests', {})
    return _game_config


def get_items() -> List[Dict[str, Any]]:
    if not _items:
        reload_all()
    return _items


def get_wall_types() -> List[Dict[str, Any]]:
    if not _wall_types:
        reload_all()
    return _wall_types


def get_enemy_types() -> List[Dict[str, Any]]:
    if not _enemy_types:
        reload_all()
    return _enemy_types


def get_map_entities() -> List[Dict[str, Any]]:
    if not _map_entities:
        reload_all()
    return _map_entities

