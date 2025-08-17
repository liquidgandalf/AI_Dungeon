# SkeletonGame/app/utils.py
import os
import socket
import netifaces
import qrcode
import pygame
from io import BytesIO


def get_local_ip():
    """Return the best local IPv4 for reaching peers on the network.

    Order of preference:
      1) SERVER_IP env var override (for container/static configs)
      2) UDP socket trick to an external address (no traffic sent)
      3) First non-loopback IPv4 via netifaces
      4) Fallback to 127.0.0.1
    """
    # 1) Env override
    env_ip = os.environ.get('SERVER_IP')
    if env_ip:
        return env_ip

    # 2) Discover via UDP socket (does not actually send data)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return ip
    except Exception:
        pass

    # 3) Fallback: first non-loopback IPv4 via netifaces
    try:
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    ip = addr.get('addr')
                    if ip and not ip.startswith('127.'):
                        return ip
    except Exception:
        pass

    # 4) Last resort
    return '127.0.0.1'


def generate_qr_surface(url: str, size: int = 200):
    qr = qrcode.make(url)
    buf = BytesIO()
    qr.save(buf, format='PNG')
    buf.seek(0)
    image = pygame.image.load(buf)
    return pygame.transform.scale(image, (size, size))
