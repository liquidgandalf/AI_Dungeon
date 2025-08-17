# AI_Dungeon/main.py
from threading import Thread
import pygame
from app.game import run_game
from app.utils import get_local_ip, generate_qr_surface
from app.server import run_server


def main():
    # Start Flask server in a background thread
    Thread(target=run_server, daemon=True).start()

    # Init pygame window
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    pygame.display.set_caption("AI_Dungeon")

    # Build QR that points phones to controller page
    ip = get_local_ip()
    url = f"http://{ip}:5050/controller"
    qr_surface = generate_qr_surface(url, size=180)
    print(f"QR points to: {url}")

    run_game(screen, qr_surface)


if __name__ == "__main__":
    main()
