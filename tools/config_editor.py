#!/usr/bin/env python3
import json
import os
import shutil
import time
from pathlib import Path

from flask import Flask, request, redirect, url_for, render_template_string, flash

APP_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = APP_ROOT / "config" / "game_config.json"
STATIC_URL = "/static"

app = Flask(__name__)
app.secret_key = os.environ.get("CONFIG_EDITOR_SECRET", "dev-secret")

VISIBILITY_MODES = [
    ("full", "Full (no fog)"),
    ("fog", "Fog of war (persistent memory)"),
    ("reveal", "Reveal radius (no memory)")
]

TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AI Dungeon — Config Editor</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; color: #111; }
      h1 { margin: 0 0 8px; }
      .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 12px 0; }
      .row { display: flex; flex-wrap: wrap; gap: 12px; }
      .col { flex: 1 1 240px; min-width: 240px; }
      label { display: block; font-weight: 600; margin-bottom: 6px; }
      input[type=number], select, input[type=text] { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 6px; }
      .checkbox { display: flex; align-items: center; gap: 8px; margin: 8px 0; }
      .actions { display: flex; gap: 12px; margin-top: 16px; }
      button, .btn { background: #0d6efd; color: white; border: 0; padding: 10px 14px; border-radius: 6px; cursor: pointer; text-decoration: none; }
      .btn.secondary { background: #6c757d; }
      .flash { padding: 10px 12px; border-radius: 6px; margin: 12px 0; }
      .flash.ok { background: #e7f7ec; color: #0f5132; border: 1px solid #badbcc; }
      .flash.err { background: #fdecea; color: #842029; border: 1px solid #f5c2c7; }
      small.hint { color: #666; display:block; margin-top: 4px; }
    </style>
  </head>
  <body>
    <h1>AI Dungeon — Config Editor</h1>
    <p>Editing: <code>{{ config_path }}</code></p>

    {% for m,cat in messages %}
      <div class="flash {{ 'ok' if cat=='ok' else 'err' }}">{{ m }}</div>
    {% endfor %}

    <form method="post" action="{{ url_for('save') }}">
      <div class="card">
        <h3>Player & Speed</h3>
        <div class="row">
          <div class="col">
            <label for="seed">Seed</label>
            <input type="text" id="seed" name="seed" value="{{ cfg.seed or '' }}" placeholder="(empty = random each run)">
            <small class="hint">Leave blank for a fresh random world each run. Set a value to make world generation reproducible.</small>
          </div>
          <div class="col">
            <label for="initial_attributes_count">Initial attribute points</label>
            <input type="number" id="initial_attributes_count" name="initial_attributes_count" value="{{ cfg.initial_attributes_count }}" min="0" step="1">
          </div>
        </div>
        <div class="row">
          <div class="col">
            <label for="speed.maxspeedpermove">Max tiles per move</label>
            <input type="number" id="speed.maxspeedpermove" name="speed.maxspeedpermove" value="{{ cfg.speed.maxspeedpermove }}" min="1" step="1">
          </div>
          <div class="col">
            <label for="speed.minspeed">Min ticks between moves</label>
            <input type="number" id="speed.minspeed" name="speed.minspeed" value="{{ cfg.speed.minspeed }}" min="0" step="1">
          </div>
          <div class="col">
            <label for="speed.max_speed_stat">Max speed stat</label>
            <input type="number" id="speed.max_speed_stat" name="speed.max_speed_stat" value="{{ cfg.speed.max_speed_stat }}" min="1" step="1">
          </div>
          <div class="col">
            <label for="speed.min_speed_stat">Min speed stat</label>
            <input type="number" id="speed.min_speed_stat" name="speed.min_speed_stat" value="{{ cfg.speed.min_speed_stat }}" min="1" step="1">
          </div>
        </div>
      </div>

      <div class="card">
        <h3>World Generation</h3>
        <div class="row">
          <div class="col">
            <label for="spawns.random_items">Random items</label>
            <input type="number" id="spawns.random_items" name="spawns.random_items" value="{{ cfg.spawns.random_items }}" min="0" step="1">
          </div>
          <div class="col">
            <label for="spawns.random_chests">Random chests</label>
            <input type="number" id="spawns.random_chests" name="spawns.random_chests" value="{{ cfg.spawns.random_chests }}" min="0" step="1">
          </div>
          <div class="col">
            <label for="spawns.random_enemies">Random enemies</label>
            <input type="number" id="spawns.random_enemies" name="spawns.random_enemies" value="{{ cfg.spawns.random_enemies }}" min="0" step="1">
          </div>
        </div>
        <div class="row">
          <div class="col">
            <label for="biomes.count">Biome count</label>
            <input type="number" id="biomes.count" name="biomes.count" value="{{ cfg.biomes.count }}" min="0" step="1">
          </div>
          <div class="col">
            <label for="biomes.radius">Biome radius</label>
            <input type="number" id="biomes.radius" name="biomes.radius" value="{{ cfg.biomes.radius }}" min="1" step="1">
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Visibility</h3>
        <div class="row">
          <div class="col">
            <label for="visibility.mode">Mode</label>
            <select id="visibility.mode" name="visibility.mode">
              {% for val,label in visibility_modes %}
                <option value="{{ val }}" {% if cfg.visibility.mode == val %}selected{% endif %}>{{ label }}</option>
              {% endfor %}
            </select>
            <small class="hint">full: no fog. fog: persistent memory. reveal: only within radius each frame.</small>
          </div>
          <div class="col">
            <label for="visibility.reveal_radius">Reveal radius</label>
            <input type="number" id="visibility.reveal_radius" name="visibility.reveal_radius" value="{{ cfg.visibility.reveal_radius }}" min="1" step="1">
          </div>
        </div>
        <div class="row">
          <div class="col">
            <label>Map markers</label>
            <div class="checkbox">
              <input type="checkbox" id="visibility.enemies" name="visibility.enemies" {% if cfg.visibility.enemies %}checked{% endif %}>
              <label for="visibility.enemies">Show enemies on big map</label>
            </div>
            <div class="checkbox">
              <input type="checkbox" id="visibility.enemy_pings" name="visibility.enemy_pings" {% if cfg.visibility.enemy_pings %}checked{% endif %}>
              <label for="visibility.enemy_pings">Show enemy radar pings</label>
            </div>
            <div class="checkbox">
              <input type="checkbox" id="visibility.enemy_pings_ignore_visibility" name="visibility.enemy_pings_ignore_visibility" {% if cfg.visibility.get('enemy_pings_ignore_visibility', False) %}checked{% endif %}>
              <label for="visibility.enemy_pings_ignore_visibility">Enemy pings ignore visibility</label>
            </div>
            <div class="checkbox">
              <input type="checkbox" id="visibility.show_chests" name="visibility.show_chests" {% if cfg.visibility.show_chests %}checked{% endif %}>
              <label for="visibility.show_chests">Show chests through fog</label>
            </div>
          </div>
        </div>
      </div>

      <div class="actions">
        <button type="submit">Save</button>
        <a class="btn secondary" href="{{ url_for('index') }}">Reload</a>
      </div>
    </form>

    <p><small>Tip: A timestamped backup is written to the same folder on save.</small></p>
  </body>
</html>
"""


def load_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict):
    # Backup existing file
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = CONFIG_PATH.with_suffix(f".json.bak.{ts}")
    shutil.copy2(CONFIG_PATH, backup)
    # Write new config with pretty formatting
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, CONFIG_PATH)


def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value):
    # HTML checkbox returns 'on' when checked, missing otherwise
    return value is not None


def nested_get(d, path, default=None):
    cur = d
    for key in path.split('.'):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def nested_set(d, path, value):
    keys = path.split('.')
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


@app.route("/", methods=["GET"])
def index():
    try:
        cfg = load_config()
        messages = [(m, 'ok') for m in list(get_flashed_messages_safe('ok'))] + [(m, 'err') for m in list(get_flashed_messages_safe('err'))]
        return render_template_string(
            TEMPLATE,
            cfg=SimpleNamespace.from_dict(cfg),
            config_path=str(CONFIG_PATH),
            visibility_modes=VISIBILITY_MODES,
            messages=messages,
        )
    except Exception as e:
        return f"Error loading config: {e}", 500


@app.route("/save", methods=["POST"])
def save():
    try:
        cfg = load_config()
        form = request.form

        # Seed (string; empty clears to None)
        raw_seed = (form.get('seed') or '').strip()
        nested_set(cfg, 'seed', (raw_seed if raw_seed != '' else None))

        # Numeric fields
        set_num(cfg, 'initial_attributes_count', form.get('initial_attributes_count'), min_val=0)
        set_num(cfg, 'speed.maxspeedpermove', form.get('speed.maxspeedpermove'), min_val=1)
        set_num(cfg, 'speed.minspeed', form.get('speed.minspeed'), min_val=0)
        set_num(cfg, 'speed.max_speed_stat', form.get('speed.max_speed_stat'), min_val=1)
        set_num(cfg, 'speed.min_speed_stat', form.get('speed.min_speed_stat'), min_val=1)
        set_num(cfg, 'spawns.random_items', form.get('spawns.random_items'), min_val=0)
        set_num(cfg, 'spawns.random_chests', form.get('spawns.random_chests'), min_val=0)
        set_num(cfg, 'spawns.random_enemies', form.get('spawns.random_enemies'), min_val=0)
        set_num(cfg, 'biomes.count', form.get('biomes.count'), min_val=0)
        set_num(cfg, 'biomes.radius', form.get('biomes.radius'), min_val=1)
        set_num(cfg, 'visibility.reveal_radius', form.get('visibility.reveal_radius'), min_val=1)

        # Selects / booleans
        mode = form.get('visibility.mode') or 'full'
        nested_set(cfg, 'visibility.mode', mode)
        nested_set(cfg, 'visibility.enemies', as_bool(form.get('visibility.enemies')))
        nested_set(cfg, 'visibility.enemy_pings', as_bool(form.get('visibility.enemy_pings')))
        nested_set(cfg, 'visibility.enemy_pings_ignore_visibility', as_bool(form.get('visibility.enemy_pings_ignore_visibility')))
        nested_set(cfg, 'visibility.show_chests', as_bool(form.get('visibility.show_chests')))

        save_config(cfg)
        flash("Saved config (backup written)", 'ok')
        return redirect(url_for('index'))
    except Exception as e:
        flash(f"Error: {e}", 'err')
        return redirect(url_for('index'))


# Helpers to bridge simple dict to dot-access in template
class SimpleNamespace(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    @classmethod
    def from_dict(cls, d):
        def convert(x):
            if isinstance(x, dict):
                ns = cls()
                for k, v in x.items():
                    ns[k] = convert(v)
                return ns
            elif isinstance(x, list):
                return [convert(v) for v in x]
            return x
        return convert(d)


def set_num(cfg, path, raw, min_val=None):
    val = as_int(raw, None)
    if val is None:
        raise ValueError(f"Invalid number for {path!r}: {raw!r}")
    if min_val is not None and val < min_val:
        raise ValueError(f"{path} must be >= {min_val}")
    nested_set(cfg, path, val)


def get_flashed_messages_safe(category):
    # Avoid importing flask's global just to keep file self-contained
    from flask import get_flashed_messages
    return get_flashed_messages(category_filter=[category])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5080"))
    host = os.environ.get("HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"[info] Config editor running at http://{host}:{port}")
    print(f"[info] Editing {CONFIG_PATH}")
    app.run(host=host, port=port, debug=debug)
