# AI_Dungeon

A local co-op dungeon crawler where players join from their phones as controllers. The desktop app renders the game with Pygame, while a Flask-SocketIO server exposes a web controller at `/controller` that mobile devices connect to over your LAN.

## Features
- Pygame-based maze world with enemies and items
- Join via phone by scanning a QR code displayed in the game
- Flask-SocketIO real-time controls (WASD arrows via mobile buttons)
- Simple inventory/equipment system

## Requirements
- Python 3.10+
- See `requirements.txt`

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
python3 main.py
```
The server starts on port 5050 and binds to all interfaces. The game window shows a QR code; scan it from your phone to open the controller page.

- Controller URL examples:
  - Desktop: http://127.0.0.1:5050/controller
  - LAN (example): http://192.168.0.10:5050/controller

## IP/Port configuration
- `SERVER_IP`: Force the IP used in the QR URL (useful if multiple NICs)
  - Example: `SERVER_IP=192.168.0.10 python3 main.py`
- `PORT`: Override the server port (defaults to 5050). If you change this, point your browser/QR to the new port.

## Troubleshooting
- UFW/Firewall: Allow TCP 5050 on your LAN
  - `sudo ufw allow 5050/tcp`
  - or restrict: `sudo ufw allow from 192.168.0.0/24 to any port 5050 proto tcp`
- Different IPs on the same machine: The server listens on all addresses; try each `192.168.0.x` your host has.
- Verify server is up:
  - `curl http://127.0.0.1:5050/controller`

## Project structure
- `main.py` – starts the Flask-SocketIO server and the Pygame loop, builds the QR code using `app/utils.py`
- `app/server.py` – Flask routes and Socket.IO events
- `app/game.py` – game world, rendering, and loop
- `app/utils.py` – local IP detection and QR surface generation
- `templates/controller.html` – mobile controller UI
- `static/` – JS/CSS/assets
- `config/` – game content JSON

## License
MIT. See `LICENSE`.
