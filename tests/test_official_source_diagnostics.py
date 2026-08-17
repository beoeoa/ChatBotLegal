from __future__ import annotations

import httpx

from api.official_source_diagnostics import (
    classify_official_response,
    classify_official_source_failure,
)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://vbpl.vn/van-ban/trung-uong")


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = _request()
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        f"status {status}", request=request, response=response
    )


def test_official_source_failures_have_granular_reason_codes() -> None:
    request = _request()
    assert (
        classify_official_source_failure(httpx.ConnectTimeout("x", request=request))
        == "OFFICIAL_SOURCE_CONNECT_TIMEOUT"
    )
    assert (
        classify_official_source_failure(httpx.ReadTimeout("x", request=request))
        == "OFFICIAL_SOURCE_READ_TIMEOUT"
    )
    assert (
        classify_official_source_failure(_status_error(403))
        == "OFFICIAL_SOURCE_FORBIDDEN"
    )
    assert (
        classify_official_source_failure(_status_error(429))
        == "OFFICIAL_SOURCE_RATE_LIMITED"
    )
    assert (
        classify_official_source_failure(_status_error(503))
        == "OFFICIAL_SOURCE_SERVER_ERROR"
    )


def test_official_response_detects_captcha_and_endpoint_shape_drift() -> None:
    request = _request()
    captcha = httpx.Response(
        200,
        request=request,
        headers={"content-type": "text/html"},
        text="<html><title>CAPTCHA verification</title></html>",
    )
    changed = httpx.Response(
        200,
        request=request,
        headers={"content-type": "application/json"},
        json={"items": []},
    )
    valid = httpx.Response(
        200,
        request=request,
        headers={"content-type": "text/x-component"},
        text='1:{"total":0,"items":[]}',
    )

    assert classify_official_response(captcha) == "OFFICIAL_SOURCE_CAPTCHA"
    assert (
        classify_official_response(changed)
        == "OFFICIAL_SOURCE_ENDPOINT_CHANGED"
    )
    assert classify_official_response(valid) is None
