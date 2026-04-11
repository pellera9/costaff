import ipaddress
import socket
from urllib.parse import urlparse

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def is_safe_url(url: str) -> bool:
    """Return False if the URL resolves to a private/loopback IP (SSRF protection)."""
    try:
        host = urlparse(url).hostname
        if not host:
            return False
        resolved = socket.getaddrinfo(host, None)
        for item in resolved:
            ip = ipaddress.ip_address(item[4][0])
            if any(ip in net for net in _PRIVATE_NETWORKS):
                return False
        return True
    except Exception:
        return False
