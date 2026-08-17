"""TLS helpers for official-source downloads.

Python distributions on Windows do not always include the same public roots
as the operating system.  Import the Windows root stores while preserving
hostname checks and ``CERT_REQUIRED``; never fall back to ``verify=False``.
"""

from __future__ import annotations

import ssl


def build_verified_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    enum_certificates = getattr(ssl, "enum_certificates", None)
    if enum_certificates is None:
        return context
    for store_name in ("ROOT", "CA"):
        try:
            certificates = enum_certificates(store_name)
        except OSError:
            continue
        for certificate, encoding, _trust in certificates:
            if encoding != "x509_asn":
                continue
            try:
                context.load_verify_locations(
                    cadata=ssl.DER_cert_to_PEM_cert(certificate)
                )
            except ssl.SSLError:
                continue
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context
