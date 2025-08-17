# AI_Dungeon/app/items.py
from typing import Dict, List, Literal, Optional, TypedDict
from .config import get_items

# Slot names
Slot = Literal[
    'head',
    'body',
    'backpack',
    'left_hand',
    'right_hand',
    'legs',
    'feet',
]

class Stats(TypedDict, total=False):
    # Core weights and durability
    weight: float
    durability: int
    # Raw strengths
    attack: int
    defense: int
    # Elements
    water_damage: int
    water_defense: int
    fire_damage: int
    fire_defense: int
    earth_damage: int
    earth_defense: int
    # Backpack capacity (if applicable)
    capacity_weight: float
    # Environmental interactions
    wall_damage: int

class ItemType(TypedDict, total=False):
    id: str
    name: str
    allowed_slots: List[Slot]
    stats: Stats
    active: bool
    # Optional enemy type id this item can spawn (used by spawners)
    spawn_type: str

# Database of item types (extensible)
ITEM_DB: Dict[str, ItemType] = {}


def register_item(item: ItemType) -> None:
    ITEM_DB[item['id']] = item


def get_item(item_id: str) -> Optional[ItemType]:
    return ITEM_DB.get(item_id)


def can_equip(item_id: str, slot: Slot) -> bool:
    it = get_item(item_id)
    return bool(it and slot in it['allowed_slots'])


def get_weight(item_id: str) -> float:
    it = get_item(item_id)
    return float(it['stats'].get('weight', 0.0)) if it else 0.0


def is_backpack(item_id: str) -> bool:
    it = get_item(item_id)
    return bool(it and 'backpack' in it['allowed_slots'])


def backpack_capacity(item_id: str) -> float:
    it = get_item(item_id)
    return float(it['stats'].get('capacity_weight', 0.0)) if it else 0.0


def _load_items_from_config():
    items = get_items()
    if not isinstance(items, list):
        return
    for it in items:
        try:
            # Expected keys in JSON: id, name, allowed_slots, stats
            item_id = it.get('id')
            name = it.get('name')
            allowed = it.get('allowed_slots', [])
            stats = it.get('stats', {})
            active = bool(it.get('active', True))
            spawn_type = it.get('spawn_type')
            if not item_id or not name:
                continue
            register_item({
                'id': str(item_id),
                'name': str(name),
                'allowed_slots': allowed,
                'stats': stats,
                'active': active,
                **({'spawn_type': str(spawn_type)} if isinstance(spawn_type, str) and spawn_type else {}),
            })
        except Exception:
            # Skip malformed entries
            continue


# Populate ITEM_DB on import
_load_items_from_config()
