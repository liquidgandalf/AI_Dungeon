# AI Dungeon — Human Readable Guide

This document summarizes what’s implemented, how to extend the game using JSON, and where to place assets (PNGs) for items and enemies.

## Dev Quickstart

- **Run**: `python -m AI_Dungeon.main` (or run `AI_Dungeon/main.py`).
- **What happens**:
  - A local Flask server starts (port 5050) and a Pygame window opens.
  - The window shows a QR code. Scan it on your phone to open the controller at `http://<your-ip>:5050/controller`.
  - Use the phone UI to move/turn. The desktop shows a top-down map (biomes, walls, players, spawners) and debug info.
- **Assets**:
  - Items: `AI_Dungeon/static/img/items/`
  - Enemies: `AI_Dungeon/static/img/enemies/`
- **Configs** (edit then restart): `AI_Dungeon/config/`
  - `game_config.json` → `biomes.count`, `biomes.radius`, spawn counts, etc.
  - `items.json` → item stats and `active` flag; includes `demon_spawn`.
  - `enemy_types.json` → enemy stats with optional `biome`/`spawner` fields.
  - `map_entities.json` → place items/enemies with sprite blocks.

## What’s implemented
- **Config-driven game data** under `AI_Dungeon/config/`:
  - `game_config.json` — core settings (attribute points, speed/cooldowns).
  - `items.json` — item definitions (stats, allowed slots). [Sprites per item can be given in map_entities for now.]
  - `wall_types.json` — wall definitions (reserved for future map/biome rules).
  - `enemy_types.json` — enemy definitions (stats; animation schema ready to adopt).
  - `map_entities.json` — world placements for items/enemies with sprite info.
- **Player profile & controls**:
  - IP-bound player stats (persist for the server run), inventory, equipment, and position restore on reconnect.
  - Speed-based cooldowns enforced on the server; clients show a countdown bar and queue one input while waiting.
  - Inventory overlay with tabs: Backpack, Stats, Loadout.
- **Rendering**:
  - Server raycaster emits column heights + lighting.
  - Billboard sprites for items/enemies: 2D images facing the player, scaled by distance, occluded by walls.
  - Walls darken progressively as they take damage (based on per-tile HP), giving a cracked look.

## Latest progress (biomes, rooms, spawners)

- **Biome system** in `app/game.py`:
  - World grid is 256x128 tiles, tile size 4 px. Biomes are generated after maze.
  - Configurable `biomes.count` and `biomes.radius` in `config/game_config.json` (defaults in `app/config.py`).
  - Centers are evenly distributed by segmenting the map; biome IDs are shuffled so colors vary per run.
  - Centers are kept at least 5 tiles from edges; we carve a circular room of radius 12 tiles at each center (outer wall preserved).
  - Biome backgrounds render as pastel colors on the desktop map; overlapping areas are blended by inverse-distance.
  - Walls render white on desktop map and in phone 3D view.
  - Players render on top; a cyan radar ping pulses on player tiles.

- **Biome metadata exposed for rendering/logic**:
  - `biome_centers: List[(cx, cy, biome_id)]` and `biome_radius` stored globally after generation for blending and spawner logic.

- **Spawners at biome centers**:
  - On first entity init, we spawn one `demon_spawn` item entity at each biome center.
  - Entity fields: `type: 'item'`, `item_id: 'demon_spawn'`, `pos: [cx+0.5, cy+0.5]`, `tile_type: 2`, `biome_id: <id>`, `sprite.image: 'items/demonspawn.png'`.
  - Grid tile remains EMPTY; `tile_type` is metadata for future interactions (e.g., destroy spawner kills its minions).

- **Items “active” flag**:
  - `config/items.json` now supports `"active"` (0/1). Random item spawns only consider items with `active == 1`.
  - Current gear items are set inactive until art is ready. Chests are spawned explicitly and unaffected.
  - New item `demon_spawn` added and active.

- **Enemy types linkage fields**:
  - `config/enemy_types.json` entries include optional `"biome"` and `"spawner"` (both can be null in the type). At runtime, enemy instances can record the spawner tile `[cx, cy]` and biome id to enable cleanup if a spawner is destroyed.

### Recent additions (enemies, collisions, phone view)

- **Enemy rendering on phone view**:
  - Enemies now render as billboard sprites in the phone 3D view using the sprite path provided by `map_entities.json` (e.g., `enemies/goblin.png`), occluded correctly by walls.

- **Collision detection**:
  - Player movement is blocked when attempting to move into a tile occupied by an enemy.
  - Enemy movement respects walls, solid cells (e.g., items/spawners), players, and other enemies.

- **Random enemy spawns via config**:
  - Enable randomized enemies at startup by adding `"random_enemies": <count>` under `spawns` in `config/game_config.json`.

- **Biome-colored sky on phone view**:
  - Server emits `sky: [r,g,b]` per frame based on the player’s current biome tile.
  - Colors are aligned with the minimap palette (1=red, 2=orange, 3=yellow, 4=green, 5=blue, 6=purple).
  - When outside any biome (biome id 0), the sky is black.
  - Palette lives in `app/game.py` → `BIOME_SKY_COLORS` and can be adjusted.

### Recent additions (walls, durability, FX)

- **Multi-hit walls with biome-scaled HP**:
  - Each wall tile has HP tracked in a parallel `wall_hp` grid (`app/game.py`).
  - Max HP per tile scales by biome ID using config: `"walls": { "hp_base": N, "hp_per_biome": M }` in `config/game_config.json`.
  - When HP reaches 0, the wall tile becomes empty.

- **Item-based wall damage (pickaxe)**:
  - If the equipped hand item has `stats.wall_damage > 0`, hand actions damage the wall in front of the player.
  - Visual feedback: wall columns are darkened proportionally to remaining HP.

- **Instance-based item durability + break handling**:
  - Each item instance lives in `players[sid]['items'][instance_id]` with fields like `{ type, durability }`.
  - `players[sid]['equipment'][slot]` stores the `instance_id` for that slot.
  - On each successful wall hit, the wall returns durability loss to the specific equipped instance. Current value is stored at `items[instance_id].durability` (initialized from the item type's `stats.durability`).
  - When durability reaches 0, the instance is unequipped and removed. A rich `equip` snapshot is emitted so the phone HUD immediately clears the slot and its bar.

- **Client FX overlays (hit feedback)**:
  - Server emits `fx` events on hit: `{ type: 'crack', cell: [x,y], level }` and `{ type: 'hit_spark' }`.
  - Client (`static/js/controls.js`) draws a quick hit spark and a short-lived screen-center crack overlay (intensity scales with `level`).
  - Designed to be simple and performant; can be later localized to the hit column for more diegetic feedback.

- **HUD right-hand icon**:
  - Phone HUD shows the currently equipped right-hand item icon (e.g., pickaxe) or a fallback glyph.
  - Durability bars for left/right hands update live on every hit via an `equip` event containing per-slot `{ id, name, durability, max_durability }`.

## Stats reference (current behavior)

- **Item stats (`config/items.json`)**
  - `durability`: starting durability for each new instance of the item type.
  - `wall_damage`: how much damage this item deals to a wall per hit (only when equipped in a hand and > 0).
  - `weight`: contributes to backpack capacity usage (where used).

- **Wall stats (`config/wall_types.json`)**
  - `durability`: the wall tile's max HP for that material (combined with biome scaling; see below).
  - `damaged` (preferred) or `damage` (fallback): how much durability the wall subtracts from the tool per hit.
  - `damage_items`: optional allow-list of item type IDs that can damage this wall. If present, only those tools are effective.

- **Game config (`config/game_config.json`)**
  - `walls.hp_base`: base HP per wall tile.
  - `walls.hp_per_biome`: additional HP per tile based on biome id. Effective max HP per tile is: `hp_base + hp_per_biome * biome_id`.

- **Live HUD updates**
  - After each durability change (including break), server emits `equip` with:
    - `equipment_instances`: map of slot -> instance_id or null.
    - `equipment`: rich slot objects: `{ id, name, durability, max_durability }` or `null`.
  - Client handler updates the left/right hand durability bars without reopening inventory.

## Directory layout (relevant bits)
- Config: `AI_Dungeon/config/`
- Client JS: `AI_Dungeon/static/js/controls.js`
- Images:
  - Items: `AI_Dungeon/static/img/items/`
  - Enemies: `AI_Dungeon/static/img/enemies/`
- Server render loop: `AI_Dungeon/app/game.py`
- Server + sockets: `AI_Dungeon/app/server.py`

## JSON files and how they work

### game_config.json
Controls initial attributes and speed/cooldown mapping.
```json
{
  "initial_attributes_count": 10,
  "speed": {
    "maxspeedpermove": 1,
    "minspeed": 3,
    "max_speed_stat": 16,
    "min_speed_stat": 1
  }
}
```

### items.json
List of item types. Example (stats depend on your design). Items support `active` (0/1) to control eligibility for random spawning:
```json
[
  {
    "id": "chest_basic",
    "name": "Wooden Chest",
    "allowed_slots": [],
    "active": 1,
    "stats": { "durability": 120 }
  },
  {
    "id": "sword_basic",
    "name": "Iron Sword",
    "allowed_slots": ["right_hand", "left_hand"],
    "active": 0,
    "stats": { "attack": 5 }
  }
]
```
Notes:
- Random item generator filters to `active == 1` and items with `allowed_slots`.
- Map-placed items (e.g., chests, spawners) define sprite blocks where placed; per-item default sprites can be added later with a `sprite` object.
 - For interactions implemented so far:
   - `stats.wall_damage` controls damage per hit applied to walls in front of the player when the item is in a hand slot.
   - `stats.durability` sets initial per-slot durability; it decrements on successful use and triggers unequip + store/drop on break.
   - `stats.weight` contributes to backpack capacity usage when items are stored.

Minimal example for a basic pickaxe (place in `items.json`):
```json
{
  "id": "pickaxe_basic",
  "name": "Basic Pickaxe",
  "allowed_slots": ["right_hand"],
  "active": 1,
  "stats": {
    "weight": 2.0,
    "durability": 50,
    "wall_damage": 1
  }
}
```

### enemy_types.json
Defines enemy stat blocks and (optionally) animation metadata. Now also supports optional biome/spawner linkage for instances. Example schema (extensible):
```json
[
  {
    "type": "goblin_basic",
    "name": "Goblin",
    "biome": null,
    "spawner": null,
    "stats": {
      "health": 120,
      "attack": 20,
      "defense": 10
    },
    "ai": {
      "notice_radius": 6.0,
      "fov_deg": 120,
      "chase_speed": 1.2,
      "flee_threshold_hp": 20,
      "attack_range": 1.0,
      "attack_cooldown_ms": 900
    }
    /*
    ,"sprite": {
      "sheet": "enemies/goblin.png",
      "base_width": 64,
      "base_height": 64,
      "scale": 1.0,
      "y_offset": 0,
      "directions": 8,
      "states": {
        "idle": { "frames": [[0,0],[1,0],[2,0],[3,0]], "frame_ms": 180 },
        "attack": { "frames": [[0,1],[1,1],[2,1],[3,1]], "frame_ms": 120 },
        "die": { "frames": [[0,2],[1,2],[2,2]], "frame_ms": 140, "loop": false }
      }
    }
    */
  }
]
```
We currently read enemy sprite metadata from `map_entities.json`. We can merge this so enemy types define their canonical sprite set.

### Biomes (desktop map rendering)
- Config: `game_config.json` → `{ "biomes": { "count": N, "radius": R } }`.
- IDs 1..6 mapped to pastel colors; 0 is background.
- Overlaps are blended; walls drawn in white above biomes; players and UI above all.
- Big circular rooms (radius 12) are carved at biome centers without breaking the exterior wall.

### wall_types.json
Defines wall materials and their gameplay effects.
- `type`, `name`, `image`: identifiers and optional sprite key.
- `stats.durability`: material base durability (used to set/refresh a tile's HP when first hit).
- `stats.damaged` (preferred) or `stats.damage`: damage dealt back to the tool's durability each hit.
- `damage_items`: optional array of item type ids that can affect this wall.

### map_entities.json
Places actual things on the map and provides sprite metadata for each placement (billboard rendering). Coordinates are in grid space (tile coordinates). Use floats for center-of-tile placement (e.g., `6.5` means center of tile 6).
```json
[
  {
    "type": "item",
    "item_id": "chest_basic",
    "pos": [6.5, 6.5],
    "sprite": {
      "image": "items/chest.png",
      "base_width": 64,
      "base_height": 64,
      "scale": 1.0,
      "y_offset": 0
    }
  },
  {
    "type": "enemy",
    "enemy_type": "goblin_basic",
    "pos": [10.5, 5.5],
    "sprite": {
      "sheet": "enemies/goblin.png",
      "base_width": 64,
      "base_height": 64,
      "scale": 1.0,
      "y_offset": 0,
      "directions": 8,
      "states": {
        "idle": { "frames": [[0,0],[1,0],[2,0],[3,0]], "frame_ms": 180 }
      }
    },
    "ai": { "state": "idle" }
  }
]
```
- `sprite.image`: path under `/static/img/` (e.g., `items/chest.png`). Used for simple one-frame items/props.
- `sprite.sheet`: sprite-sheet path for animated/directional enemies under `/static/img/` (e.g., `enemies/goblin.png`).
- `base_width/height`: cell size (pixels) in the source image.
- `scale`: final billboard scale multiplier.
- `y_offset`: shifts sprite up/down on screen.
- `directions`: planned for directional facings (4 or 8). We’ll select the nearest facing by player angle.
- `states`: animation definitions per state with frame grid coords and durations.

## Where images go
- Place item images under:
  - `AI_Dungeon/static/img/items/`
- Place enemy sprite-sheets under:
  - `AI_Dungeon/static/img/enemies/`

The client looks up images at `/static/img/<path>`. For example, `items/chest.png` resolves to `AI_Dungeon/static/img/items/chest.png`.

## Adding new content

### Add an item pickup or prop
1. Drop the PNG at `AI_Dungeon/static/img/items/your_item.png`.
2. Define the item in `AI_Dungeon/config/items.json` (id, name, stats, allowed_slots).
3. Place it on the map in `AI_Dungeon/config/map_entities.json` with a block like:
```json
{
  "type": "item",
  "item_id": "your_item_id",
  "pos": [X, Y],
  "sprite": { "image": "items/your_item.png", "base_width": 64, "base_height": 64, "scale": 1.0, "y_offset": 0 }
}
```
4. Restart the server (or add a hot-reload later) and re-join.

### Add an enemy
1. Drop the sprite-sheet (PNG) at `AI_Dungeon/static/img/enemies/your_enemy.png`.
2. Define the enemy type in `AI_Dungeon/config/enemy_types.json` (stats and optional AI defaults).
3. Place it on the map in `AI_Dungeon/config/map_entities.json` with `type: "enemy"` and a `sprite.sheet` definition plus basic `states`.
4. Restart and re-join.

## Known next steps (planned)
- **Directional facings** for enemies (4/8-direction) based on player-relative angle.
- **Enemy AI** (idle → notice → chase/attack → flee → die) driven by config.
- **Spawner behavior**: periodic enemy spawn per center, link enemies to `spawner` for cleanup on destruction.
- **Pickup and chest interactions** (use action near entity to pick up or open container).
- **Per-item sprite defaults** in `items.json` to avoid duplicating sprite blocks in `map_entities.json`.
- **Admin tools** to reload JSON configs at runtime.

## Troubleshooting
- If a sprite doesn’t render:
  - Check the `sprite.image`/`sprite.sheet` path relative to `/static/img/`.
  - Ensure `base_width`/`base_height` match the spritesheet cell size.
  - Verify the entity is within the player’s FOV and not occluded by walls.
- If the canvas is black: verify a `frame` event is coming from the server and that the controller has joined.

## Files to reference
- Renderer: `AI_Dungeon/app/game.py` — billboard projection, occlusion, frame emission.
- Client draw: `AI_Dungeon/static/js/controls.js` — image cache and sprite drawing.
- Config loader: `AI_Dungeon/app/config.py` — reads all JSON configs.
- Server/players: `AI_Dungeon/app/server.py` — cooldowns, state, and sockets.

