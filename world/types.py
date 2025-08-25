from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Tuple, Any


@dataclass
class World:
    """Minimal placeholder world structure.
    We keep this very light for Phase 1. Later phases will flesh this out
    with tiles, entities, biomes, etc.
    """
    width: int
    height: int
    # Optional future fields to avoid churn in call sites
    meta: Optional[dict] = None
    tiles: Optional[List[List[int]]] = None
    biomes: Optional[List[List[int]]] = None
    entities: Optional[List[dict]] = None
