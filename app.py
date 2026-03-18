from __future__ import annotations

import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from db import init_db, seed_demo_data
from orders import ApiError, create_order


MOCK_GEOCODES = {
    # Some common US postal codes so you can test the distance logic.
    "10001": {"lat": 40.7506, "lng": -73.9970},  # New York
    "60601": {"lat": 41.8864, "lng": -87.6186},  # Chicago
    "94105": {"lat": 37.7898, "lng": -122.3942},  # San Francisco
    "90001": {"lat": 33.9739, "lng": -118.2487},  # Los Angeles
}


class OrderApiServer(ThreadingHTTPServer):
    """Just ThreadingHTTPServer plus a couple of extra fields we need."""

    db_path: str
    base_url: str


class Handler(BaseHTTPRequestHandler):
    server: OrderApiServer
    server_version = "OrderApi/0.1"

    def do_POST(self) -> None:
        if self.path == "/orders":
            self.handle_create_order()
            return

        # These mock routes exist only so the main order flow can make real HTTP calls
        # using urllib.request. In a real app, these would be real external services.
        if self.path == "/_mock/geocode":
            self.handle_mock_geocode()
            return

        if self.path == "/_mock/payments":
            self.handle_mock_payments()
            return

        self.send_json(404, {"error": "not found"})

    def handle_create_order(self) -> None:
        try:
            payload = self.read_json_body()
            result = create_order(
                payload=payload,
                db_path=self.server.db_path,
                base_url=self.server.base_url,
            )
            self.send_json(201, result)
        except ApiError as exc:
            self.send_json(exc.status_code, {"error": exc.message})
        except json.JSONDecodeError:
            self.send_json(400, {"error": "invalid JSON"})
        except Exception:
            # Keep the public error boring.
            self.send_json(500, {"error": "internal server error"})

    def handle_mock_geocode(self) -> None:
        payload = self.read_json_body()
        address = payload.get("address") if isinstance(payload, dict) else None
        postal_code = address.get("postal_code") if isinstance(address, dict) else None

        if not isinstance(postal_code, str):
            self.send_json(400, {"error": "address.postal_code is required"})
            return

        coords = MOCK_GEOCODES.get(postal_code)
        if coords is None:
            self.send_json(400, {"error": f"no mock geocode for postal code {postal_code}"})
            return

        self.send_json(200, coords)

    def handle_mock_payments(self) -> None:
        payload = self.read_json_body()
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "invalid body"})
            return

        card_number = payload.get("credit_card_number")
        amount_cents = payload.get("amount_cents")
        description = payload.get("description")

        if not isinstance(card_number, str) or not card_number:
            self.send_json(400, {"error": "credit_card_number is required"})
            return
        if not isinstance(amount_cents, int) or amount_cents <= 0:
            self.send_json(400, {"error": "amount_cents must be a positive integer"})
            return
        if not isinstance(description, str) or not description:
            self.send_json(400, {"error": "description is required"})
            return

        # A very dumb mock rule for testing payment failures.
        if card_number == "4000000000000002":
            self.send_json(402, {"error": "card declined"})
            return

        self.send_json(
            200,
            {
                "payment_id": f"pay_{uuid.uuid4().hex[:12]}",
                "status": "approved",
            },
        )

    def read_json_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        return json.loads(raw_body or b"{}")

    def send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str, port: int, db_path: str) -> OrderApiServer:
    init_db(db_path)
    seed_demo_data(db_path)

    server = OrderApiServer((host, port), Handler)
    actual_port = int(server.server_address[1])

    server.db_path = db_path
    # The app calls its own mock endpoints over HTTP.
    # Force localhost here so those calls work even if someone binds 0.0.0.0.
    server.base_url = f"http://127.0.0.1:{actual_port}"
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Tiny order API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="./data/app.db")
    args = parser.parse_args()

    server = make_server(args.host, args.port, args.db)
    print(f"Listening on {server.base_url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
