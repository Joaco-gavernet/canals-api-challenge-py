from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class HttpClientError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Tiny HTTP helper using only the Python standard library."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw_body = response.read()
            return json.loads(raw_body or b"{}")
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        try:
            payload = json.loads(raw_body or b"{}")
            message = str(payload.get("error") or payload.get("message") or exc.reason)
        except json.JSONDecodeError:
            message = str(exc.reason)
        raise HttpClientError(exc.code, message) from exc


def geocode_address(base_url: str, address: dict[str, Any]) -> dict[str, float]:
    # TO-DO: replace this mock endpoint with a real geocoding provider.
    return post_json(f"{base_url}/_mock/geocode", {"address": address})


def charge_payment(
    base_url: str,
    credit_card_number: str,
    amount_cents: int,
    description: str,
) -> dict[str, Any]:
    # TO-DO: replace this mock endpoint with a real payment provider.
    return post_json(
        f"{base_url}/_mock/payments",
        {
            "credit_card_number": credit_card_number,
            "amount_cents": amount_cents,
            "description": description,
        },
    )
