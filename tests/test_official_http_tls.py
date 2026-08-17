import ssl

from api.official_http import build_verified_ssl_context


def test_official_download_uses_verified_system_trust_not_insecure_tls():
    context = build_verified_ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
