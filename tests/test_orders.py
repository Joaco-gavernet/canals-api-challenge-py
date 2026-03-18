from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app import make_server
from db import connect_db


class OrderApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "app.db")

        self.server = make_server("127.0.0.1", 0, self.db_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def post_json(self, path: str, payload: dict) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def make_order_payload(
        self,
        postal_code: str,
        items: list[dict],
        card_number: str = "4242424242424242",
    ) -> dict:
        return {
            "customer": {
                "name": "Coco",
                "email": "coco@example.com",
            },
            "shipping_address": {
                "line1": "123 Test St",
                "city": "Test City",
                "state": "TS",
                "postal_code": postal_code,
                "country": "US",
            },
            "items": items,
            "payment": {
                "credit_card_number": card_number,
            },
        }

    def test_create_order_picks_nearest_warehouse(self) -> None:
        status, body = self.post_json(
            "/orders",
            self.make_order_payload(
                postal_code="10001",
                items=[
                    {"product_id": 1, "quantity": 1},
                    {"product_id": 2, "quantity": 2},
                ],
            ),
        )

        self.assertEqual(status, 201)
        self.assertEqual(body["warehouse_id"], 1)
        self.assertEqual(body["total_cents"], 2500)
        self.assertEqual(body["status"], "created")

    def test_create_order_uses_only_warehouse_that_has_everything(self) -> None:
        status, body = self.post_json(
            "/orders",
            self.make_order_payload(
                postal_code="94105",
                items=[
                    {"product_id": 1, "quantity": 1},
                    {"product_id": 2, "quantity": 1},
                    {"product_id": 3, "quantity": 1},
                ],
            ),
        )

        self.assertEqual(status, 201)
        self.assertEqual(body["warehouse_id"], 2)
        self.assertEqual(body["total_cents"], 4500)

    def test_create_order_returns_409_when_no_warehouse_can_fulfill(self) -> None:
        status, body = self.post_json(
            "/orders",
            self.make_order_payload(
                postal_code="10001",
                items=[{"product_id": 2, "quantity": 999}],
            ),
        )

        self.assertEqual(status, 409)
        self.assertIn("no warehouse", body["error"])

    def test_create_order_returns_402_when_payment_is_declined(self) -> None:
        status, body = self.post_json(
            "/orders",
            self.make_order_payload(
                postal_code="10001",
                items=[{"product_id": 1, "quantity": 1}],
                card_number="4000000000000002",
            ),
        )

        self.assertEqual(status, 402)
        self.assertIn("declined", body["error"])

    def test_successful_order_decrements_inventory(self) -> None:
        status, body = self.post_json(
            "/orders",
            self.make_order_payload(
                postal_code="10001",
                items=[{"product_id": 1, "quantity": 3}],
            ),
        )

        self.assertEqual(status, 201)

        conn = connect_db(self.db_path)
        try:
            remaining = conn.execute(
                "SELECT quantity FROM inventory WHERE warehouse_id = ? AND product_id = ?",
                (1, 1),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(remaining, 7)


if __name__ == "__main__":
    unittest.main()
