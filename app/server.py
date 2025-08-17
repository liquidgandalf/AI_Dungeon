# SkeletonGame/app/server.py
import os
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from .items import get_item
from .config import get_game_config

# Resolve directories relative to this file
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
template_dir = os.path.join(base_dir, 'templates')
static_dir = os.path.join(base_dir, 'static')

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins='*')

# Minimal player registry used by the pygame loop
players = {}
ip_stats = {}  # Persist stat allocations per client IP
remembered_names = {}  # ip -> name
ip_profiles = {}  # ip -> persisted profile (stats/equipment/inventory/cell/angle)


def random_alloc_stats(total: int = 24, cap: int = 5):
    # 8 keys as requested
    keys = [
        'attack', 'defense',
        'water_damage', 'water_defense',
        'fire_damage', 'fire_defense',
        'earth_damage', 'earth_defense',
    ]
    stats = {k: 0 for k in keys}
    # distribute points one by one, respecting cap
    import random as _r
    remaining = total
    choices = keys[:]
    while remaining > 0 and choices:
        k = _r.choice(choices)
        if stats[k] < cap:
            stats[k] += 1
            remaining -= 1
        # refresh available choices (avoid infinite loop if all are at cap)
        choices = [kk for kk in keys if stats[kk] < cap]
    return stats


def _move_interval_seconds(stats: dict) -> float:
    cfg = get_game_config()
    sp = cfg.get('speed', {})
    max_per = float(sp.get('maxspeedpermove', 1))
    min_per = float(sp.get('minspeed', 3))
    max_stat = int(sp.get('max_speed_stat', 16))
    min_stat = int(sp.get('min_speed_stat', 1))
    speed_stat = int((stats or {}).get('speed', 1))
    # clamp
    speed_stat = max(min_stat, min(max_stat, speed_stat))
    # map speed_stat in [min_stat..max_stat] to seconds in [min_per..max_per] inversely
    # higher speed -> lower seconds per move
    if max_stat == min_stat:
        return max_per
    t = (speed_stat - min_stat) / (max_stat - min_stat)
    return min_per + (max_per - min_per) * t


def _emit_cooldown(sid: str):
    p = players.get(sid)
    if not p:
        return
    now = time.time()
    ready_at = float(p.get('next_ready_ts', now))
    duration = _move_interval_seconds(p.get('stats', {}))
    socketio.emit('cooldown', {
        'now': now,
        'ready_at': ready_at,
        'duration': duration,
    }, to=sid)


def _process_control(sid: str, cmd: str):
    if sid not in players:
        return
    players[sid]['pending'] = cmd
    # Movement commands no longer use cooldown; process immediately in game loop


def _process_action(sid: str, button: str):
    # present behavior: inventory sends state; left/right are placeholders
    if sid not in players:
        return
    if button == 'inventory':
        # emit state immediately
        p = players[sid]
        inv_names = [{'id': iid, 'name': (get_item(iid) or {}).get('name', iid)} for iid in p['inventory']]
        eq = {k: (v and {'id': v, 'name': (get_item(v) or {}).get('name', v)}) for k, v in p['equipment'].items()}
        emit('state', {
            'stats': p['stats'],
            'equipment': eq,
            'inventory': inv_names,
        }, to=sid)
    # set cooldown regardless
    players[sid]['next_ready_ts'] = time.time() + _move_interval_seconds(players[sid]['stats'])
    _emit_cooldown(sid)


def _queue_and_schedule(sid: str, kind: str, payload: dict):
    p = players.get(sid)
    if not p:
        return
    p['queued'] = {'kind': kind, 'payload': payload}

    def waiter(sid_local: str):
        while True:
            pp = players.get(sid_local)
            if not pp:
                return
            now = time.time()
            ready_at = float(pp.get('next_ready_ts', now))
            if now >= ready_at:
                q = pp.pop('queued', None)
                if not q:
                    return
                if q['kind'] == 'control':
                    _process_control(sid_local, q['payload'].get('command'))
                elif q['kind'] == 'action':
                    _process_action(sid_local, q['payload'].get('button'))
                return
            time.sleep(0.05)

    # start background waiter
    socketio.start_background_task(waiter, sid)


@app.route('/controller')
def controller():
    client_ip = request.remote_addr
    default_name = remembered_names.get(client_ip, '') if client_ip else ''
    return render_template('controller.html', default_name=default_name)

@socketio.on('connect')
def on_connect():
    print('Client connected')

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    if sid in players:
        # persist profile by IP so the player can resume
        client_ip = request.remote_addr or 'unknown'
        p = players[sid]
        ip_profiles[client_ip] = {
            'name': p.get('name'),
            'stats': p.get('stats', {}),
            'equipment': p.get('equipment', {}),
            'inventory': p.get('inventory', []),
            'backpack_weight_used': p.get('backpack_weight_used', 0.0),
            'cell': p.get('cell'),  # populated by game loop each frame
            'angle': p.get('angle'),
        }
        remembered_names[client_ip] = p.get('name', remembered_names.get(client_ip, ''))
        print(f"Client disconnected: {players[sid]['name']} ({sid})")
        del players[sid]

@socketio.on('join')
def on_join(data):
    name = (data or {}).get('name', '').strip() or 'Player'
    sid = request.sid
    client_ip = request.remote_addr or 'unknown'
    # reuse remembered name if available; otherwise remember provided name
    if client_ip in remembered_names:
        name = remembered_names[client_ip]
    else:
        remembered_names[client_ip] = name
    # get or create IP-bound stats
    if client_ip not in ip_stats:
        cfg = get_game_config()
        total_pts = int(cfg.get('initial_attributes_count', 10))
        ip_stats[client_ip] = random_alloc_stats(total=total_pts, cap=5)
    core_stats = dict(ip_stats[client_ip])
    # add base non-rolled stats
    core_stats.update({
        'backpack_size': 1,
        'strength': 1,
        'speed': 1,
    })
    # Check for a persisted profile for this IP
    persisted = ip_profiles.get(client_ip, {})

    players[sid] = {
        'name': name,
        'pending': None,   # one-step move direction requested by controller
        # inventory system
        'equipment': {
            'head': None,
            'body': None,
            'backpack': None,
            'left_hand': None,
            'right_hand': None,
            'legs': None,
            'feet': None,
        },
        'inventory': persisted.get('inventory', []),   # list of item_ids
        'backpack_weight_used': persisted.get('backpack_weight_used', 0.0),
        # base player stats (IP-bound core stats + base misc), prefer persisted overrides
        'stats': {**core_stats, **persisted.get('stats', {})},
        'last_active': time.time(),
        'next_ready_ts': time.time(),
        # Hint to game loop to restore last known position/orientation
        'restore': {
          'cell': persisted.get('cell'),
          'angle': persisted.get('angle'),
        }
    }
    # Restore equipment if available
    if 'equipment' in persisted:
        players[sid]['equipment'].update(persisted['equipment'] or {})
    # If no persisted right-hand item, equip a default pickaxe
    if not players[sid]['equipment'].get('right_hand'):
        players[sid]['equipment']['right_hand'] = 'pickaxe_basic'
    print(f"Player joined: {name} ({sid})")
    emit('joined', {'ok': True})
    # Send lightweight equipment snapshot for HUD (no overlay)
    eq_ids = {k: v for k, v in players[sid]['equipment'].items()}
    emit('equip', {
        'equipment': eq_ids,
    }, to=sid)
    _emit_cooldown(sid)

@socketio.on('control')
def on_control(data):
    # data: {'command': 'up'|'down'|'left'|'right'}
    cmd = (data or {}).get('command')
    sid = request.sid
    if sid not in players:
        return
    # Movement is smooth: process immediately, no cooldown gating
    _process_control(sid, cmd)

@socketio.on('action')
def on_action(data):
    # data: {'button': 'left'|'right'|'inventory'}
    btn = (data or {}).get('button')
    sid = request.sid
    if sid not in players:
        return
    players[sid]['last_active'] = time.time()
    # For now: on inventory, send a full state snapshot to the client
    if btn == 'inventory':
        p = players[sid]
        # resolve inventory item names
        inv = [{
            'id': iid,
            'name': (get_item(iid) or {}).get('name', iid)
        } for iid in (p.get('inventory') or [])]
        # resolve equipped item names
        eq = {}
        for slot, iid in (p.get('equipment') or {}).items():
            if iid:
                it = get_item(iid)
                eq[slot] = {
                    'id': iid,
                    'name': (it or {}).get('name', iid)
                }
            else:
                eq[slot] = None
        emit('state', {
            'stats': p.get('stats', {}),
            'equipment': eq,
            'inventory': inv,
        })
    elif btn in ('left', 'right'):
        # record a pending hand action to be processed by the game loop
        players[sid]['pending_action'] = btn  # 'left' or 'right'
        # start action cooldown
        players[sid]['next_ready_ts'] = time.time() + _move_interval_seconds(players[sid].get('stats', {}))
        _emit_cooldown(sid)
        return

    # start cooldown for inventory as well
    players[sid]['next_ready_ts'] = time.time() + _move_interval_seconds(players[sid].get('stats', {}))
    _emit_cooldown(sid)


def run_server():
    socketio.run(app, host='0.0.0.0', port=5050, allow_unsafe_werkzeug=True)
