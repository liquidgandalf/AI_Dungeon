# Refactor Plan: Simplify game.py and Introduce world/

Branch: `simplify_maingame`
Goal: Reduce `app/game.py` (3800+ lines) by extracting world generation/loading and clarifying game loop responsibilities without breaking features.

---

## Guiding Principles
- Minimize behavior changes; refactor behind stable interfaces.
- Strangler pattern: introduce seams, move code behind them, then delete old code.
- Small, reviewable commits with runnable checkpoints.
- Add smoke tests to catch regressions early.

---

## Target Structure (incremental)
- `world/`
  - `__init__.py`
  - `types.py` (dataclasses: World, Tile, Entity, Item, Spawn, etc.)
  - `schema.py` (pydantic/voluptuous validation for map JSON)
  - `provider.py` (interface: `WorldProvider`, `get_world(seed|path|config)`)
  - `generator.py` (current random world gen moved here; non-breaking)
  - `loader.py` (load pre-generated maps from `world/maps/*.json`)
  - `maps/` (sample: `demo_level_1.json`)
- `app/`
  - `engine.py` (GameEngine: update, render, tick separation) [later]
  - `render.py` (raycast/draw utilities detached from state) [later]
  - `state.py` (game state containers, player registry) [later]

---

## Phase 0 — Baseline and Safety Nets
- [ ] Add minimal smoke tests (no window):
  - [ ] Import `main.py` without side effects
  - [ ] One engine tick with mocked Pygame
  - [ ] Socket route `/controller` responds 200
- [ ] Scripted run: `scripts/run.sh` baseline launch
- [ ] Enable simple logging in critical paths

Output: tests pass on `main` and the new branch.

---

## Phase 1 — Introduce World Provider Seam (no behavior change)
- [ ] Create `world/provider.py` with `WorldProvider` protocol
- [ ] Extract world-related types to `world/types.py`
- [ ] Wrap existing inline world generation in an adapter called from `game.py` via provider interface
- [ ] Config: add `world.source` = `random` | `prebuilt`, and `world.map_path` (optional)
- [ ] Wire `game.py` to request a world via provider, default to `random`

Output: Game runs exactly as before using the provider seam.

---

## Phase 2 — Move Random Generation Out of game.py
- [ ] Move generation functions/consts from `app/game.py` into `world/generator.py`
- [ ] Keep algorithm as-is; remove dead globals; return `World` dataclass
- [ ] Update `game.py` to consume `World` instead of raw internal structures

Output: Large chunk removed from `game.py`; behavior unchanged.

---

## Phase 3 — Add Prebuilt World Loader
- [ ] Define JSON schema in `world/schema.py`
- [ ] Implement `world/loader.py` to read and validate `world/maps/*.json`
- [ ] Create `world/maps/demo_level_1.json` equivalent to current default layout
- [ ] Add CLI/env/config switch to choose loader (e.g., `WORLD_SOURCE=prebuilt`)

Output: Main app can load a pre-generated map; no on-load generation required.

---

## Phase 4 — Start Game Loop/Rendering Separation (optional if time)
- [ ] Introduce `app/engine.py` with `GameEngine` (tick/update/render)
- [ ] Move render helpers to `app/render.py`
- [ ] Keep `main.py` orchestration only

Output: Clear boundaries; easier to test and extend.

---

## Phase 5 — Cleanup and Deletion
- [ ] Remove remaining generation code from `game.py`
- [ ] Prune unused globals; centralize config reading
- [ ] Update docs: `README.md`, `human_read.md`

---

## Tests to Add/Keep
- [ ] Config validation for `config/*.json` and `world/maps/*.json`
- [ ] Smoke: start server, hit `/controller`
- [ ] Deterministic gen (seed) produces stable `World` shape

---

## Risks & Mitigations
- **Risk:** Hidden coupling in `game.py` to generation internals
  - Mitigation: Introduce `World` dataclass with minimal surface, add adapter shims
- **Risk:** Performance regressions
  - Mitigation: Keep algorithms identical during move; measure FPS before/after
- **Risk:** JSON schema drift
  - Mitigation: Centralize schema in `world/schema.py` with validation tests

---

## Rollout Plan
1. Phase 1 PR: provider seam + no-op behavior change
2. Phase 2 PR: move generator; shrink `game.py`
3. Phase 3 PR: prebuilt loader + sample map
4. Phase 4 PR: engine/render split (optional)
5. Phase 5 PR: cleanup + docs

Each PR: small, passes smoke tests, manual LAN controller check.

---

## Commands
- Create maps dir: `mkdir -p world/maps`
- Choose source: `WORLD_SOURCE=prebuilt python3 main.py` or config option
- Push branch: `git push -u origin simplify_maingame`
