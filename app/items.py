# AI_Dungeon/app/items.py
from typing import Dict, List, Literal, Optional, TypedDict, Any
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
    # Optional icon path relative to /static/img/ (e.g., 'items/pickaxe.png')
    icon: str
    # Special/unique world item flag
    special: bool
    # Container support
    container: bool
    numberitems: int
    maycontain: List[Dict[str, Any]]  # entries: { item, weight, min, max }

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
            icon = it.get('icon')
            special = bool(it.get('special', False))
            # Container fields (optional)
            container = bool(it.get('container', False))
            numberitems = it.get('numberitems')
            maycontain = it.get('maycontain')
            if not item_id or not name:
                continue
            reg: ItemType = {
                'id': str(item_id),
                'name': str(name),
                'allowed_slots': allowed,
                'stats': stats,
                'active': active,
            }
            if isinstance(spawn_type, str) and spawn_type:
                reg['spawn_type'] = str(spawn_type)
            if isinstance(icon, str) and icon:
                reg['icon'] = str(icon)
            if special:
                reg['special'] = True
            if container:
                reg['container'] = True
                if isinstance(numberitems, int) and numberitems > 0:
                    reg['numberitems'] = int(numberitems)
                if isinstance(maycontain, list):
                    reg['maycontain'] = maycontain  # keep raw for game logic to interpret
            register_item(reg)
        except Exception:
            # Skip malformed entries
            continue


# Populate ITEM_DB on import
_load_items_from_config()


def get_item_icon(item_id: str) -> Optional[str]:
    """Return the configured icon path for an item (relative to /static/img/), if any."""
    it = get_item(item_id)
    if not it:
        return None
    icon = it.get('icon')  # type: ignore[assignment]
    return icon if isinstance(icon, str) and icon else None


def get_item_icons_map() -> Dict[str, str]:
    """Return a mapping of item_id -> icon path for items that define an icon."""
    out: Dict[str, str] = {}
    for iid, it in ITEM_DB.items():
        icon = it.get('icon')
        if isinstance(icon, str) and icon:
            out[iid] = icon
    return out
