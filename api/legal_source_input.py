"""Source references for human review, separate from network fetch permission."""
from urllib.parse import urlsplit
import ipaddress


def valid_source_reference(value: str) -> bool:
    try:
        parsed = urlsplit(str(value or '').strip())
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username or parsed.password:
            return False
        if host == 'localhost' or host.endswith(('.localhost', '.local')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return '.' in host and not any(c.isspace() for c in host)
    except ValueError:
        return False
