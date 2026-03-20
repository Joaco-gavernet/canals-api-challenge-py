from __future__ import annotations

from typing import Any

from clients import HttpClientError, charge_payment, geocode_address
from db import connect_db
from distance import haversine_km


class ApiError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BadRequest(ApiError):
    status_code = 400


class PaymentDeclined(ApiError):
    status_code = 402


class Conflict(ApiError):
    status_code = 409


class NotFound(ApiError):
    status_code = 404


REQUIRED_ADDRESS_FIELDS = [
    "line1",
    "city",
    "state",
    "postal_code",
    "country",
]


def create_order(payload: Any, db_path: str, base_url: str) -> dict[str, Any]:
    """
    Main order flow.

    Keep the HTTP layer dumb.
    Keep the business rules here where they are easy to read.
    """
    order = validate_create_order(payload)

    # Mocked external call over HTTP.
    try:
        shipping_coords = geocode_address(base_url, order["shipping_address"])
    except HttpClientError as exc:
        raise BadRequest(f"could not geocode shipping address: {exc.message}") from exc

    conn = connect_db(db_path)
    try:
        products = load_products(conn, order["items"])
        total_cents = calculate_total_cents(order["items"], products)
        candidates = find_candidate_warehouses(conn, order["items"])
    finally:
        conn.close()

    warehouse = choose_nearest_warehouse(candidates, shipping_coords)

    # Charge the card before we start the write transaction.
    # This keeps the DB transaction short.
    # TO-DO: in a real system, add idempotency and compensation/refund logic.
    try:
        payment = charge_payment(
            base_url=base_url,
            credit_card_number=order["payment"]["credit_card_number"],
            amount_cents=total_cents,
            description=f"Order for {order['customer']['name']}",
        )
    except HttpClientError as exc:
        if exc.status_code == 402:
            raise PaymentDeclined(f"payment declined: {exc.message}") from exc
        raise ApiError(f"payment service error: {exc.message}") from exc

    conn = connect_db(db_path)
    try:
        # IMMEDIATE grabs a write lock up front.
        # That makes concurrent inventory changes less surprising.
        conn.execute("BEGIN IMMEDIATE")

        order_id = insert_order(
            conn=conn,
            order=order,
            shipping_coords=shipping_coords,
            warehouse_id=warehouse["id"],
            total_cents=total_cents,
            payment=payment,
        )
        insert_order_items(conn, order_id, order["items"], products)
        decrement_inventory(conn, warehouse["id"], order["items"])

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "id": order_id,
        "status": "created",
        "warehouse_id": warehouse["id"],
        "total_cents": total_cents,
        "payment_id": payment["payment_id"],
    }


def validate_create_order(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BadRequest("request body must be a JSON object")

    customer = payload.get("customer")
    if not isinstance(customer, dict):
        raise BadRequest("customer is required")

    customer_name = require_string(customer, "name")
    customer_email = require_string(customer, "email")

    shipping_address = payload.get("shipping_address")
    if not isinstance(shipping_address, dict):
        raise BadRequest("shipping_address is required")

    normalized_address: dict[str, str] = {}
    for field in REQUIRED_ADDRESS_FIELDS:
        normalized_address[field] = require_string(shipping_address, field)

    raw_items = payload.get("items")
    items = normalize_items(raw_items)

    payment = payload.get("payment")
    if not isinstance(payment, dict):
        raise BadRequest("payment is required")

    credit_card_number = require_string(payment, "credit_card_number")
    if not credit_card_number.isdigit():
        raise BadRequest("payment.credit_card_number must contain only digits")

    return {
        "customer": {
            "name": customer_name,
            "email": customer_email,
        },
        "shipping_address": normalized_address,
        "items": items,
        "payment": {
            # Keep it only in memory long enough to call the payment API.
            # Do NOT store raw card numbers in the database.
            "credit_card_number": credit_card_number,
        },
    }


def require_string(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BadRequest(f"{key} is required")
    return value.strip()


def normalize_items(raw_items: Any) -> list[dict[str, int]]:
    """
    Merge duplicate product lines.

    Example:
    [{product_id: 1, quantity: 1}, {product_id: 1, quantity: 2}]
    becomes
    [{product_id: 1, quantity: 3}]
    """
    if not isinstance(raw_items, list) or len(raw_items) == 0:
        raise BadRequest("items must be a non-empty array")

    totals_by_product_id: dict[int, int] = {}

    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise BadRequest(f"items[{index}] must be an object")

        product_id = item.get("product_id")
        quantity = item.get("quantity")

        if not isinstance(product_id, int) or product_id <= 0:
            raise BadRequest(f"items[{index}].product_id must be a positive integer")
        if not isinstance(quantity, int) or quantity <= 0:
            raise BadRequest(f"items[{index}].quantity must be a positive integer")

        totals_by_product_id[product_id] = totals_by_product_id.get(product_id, 0) + quantity

    return [
        {"product_id": product_id, "quantity": quantity}
        for product_id, quantity in sorted(totals_by_product_id.items())
    ]


def load_products(conn, items: list[dict[str, int]]) -> dict[int, dict[str, Any]]:
    product_ids = [item["product_id"] for item in items]
    placeholders = ", ".join("?" for _ in product_ids) # helps avoid SQL injection vulnerability

    rows = conn.execute(
        f"SELECT id, name, price_cents FROM products WHERE id IN ({placeholders})",
        product_ids,
    ).fetchall()

    products = {
        int(row["id"]): {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "price_cents": int(row["price_cents"]),
        }
        for row in rows
    }

    missing_ids = [product_id for product_id in product_ids if product_id not in products]
    if missing_ids:
        raise NotFound(f"unknown product ids: {', '.join(str(x) for x in missing_ids)}")

    return products


def calculate_total_cents(
    items: list[dict[str, int]],
    products: dict[int, dict[str, Any]],
) -> int:
    total_cents = 0
    for item in items:
        product = products[item["product_id"]]
        total_cents += product["price_cents"] * item["quantity"]
    return total_cents


def find_candidate_warehouses(conn, items: list[dict[str, int]]) -> list[dict[str, Any]]:
    """
    Find warehouses that can fulfill ALL requested items.

    The trick:
    - build a virtual table with the requested products/quantities
    - join inventory against it
    - keep only warehouses that matched every requested row
    """
    value_placeholders = ", ".join("(?, ?)" for _ in items)
    params: list[int] = []
    for item in items:
        params.extend([item["product_id"], item["quantity"]])

    sql = f"""
    WITH requested(product_id, qty) AS (
        VALUES {value_placeholders}
    )
    SELECT w.id, w.name, w.lat, w.lng
    FROM warehouses AS w
    JOIN inventory AS i
      ON i.warehouse_id = w.id
    JOIN requested AS r
      ON r.product_id = i.product_id
    WHERE i.quantity >= r.qty
    GROUP BY w.id
    HAVING COUNT(*) = (SELECT COUNT(*) FROM requested)
    ORDER BY w.id
    """

    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "lat": float(row["lat"]),
            "lng": float(row["lng"]),
        }
        for row in rows
    ]


def choose_nearest_warehouse(
    candidates: list[dict[str, Any]],
    shipping_coords: dict[str, float],
) -> dict[str, Any]:
    if not candidates:
        raise Conflict("no warehouse can fulfill all requested items")

    best = min(
        candidates,
        key=lambda warehouse: haversine_km(
            warehouse["lat"],
            warehouse["lng"],
            shipping_coords["lat"],
            shipping_coords["lng"],
        ),
    )
    return best


def insert_order(
    conn,
    order: dict[str, Any],
    shipping_coords: dict[str, float],
    warehouse_id: int,
    total_cents: int,
    payment: dict[str, Any],
) -> int:
    address = order["shipping_address"]
    customer = order["customer"]

    cursor = conn.execute(
        """
        INSERT INTO orders (
            customer_name,
            customer_email,
            shipping_line1,
            shipping_city,
            shipping_state,
            shipping_postal_code,
            shipping_country,
            shipping_lat,
            shipping_lng,
            warehouse_id,
            total_cents,
            payment_id,
            payment_status,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer["name"],
            customer["email"],
            address["line1"],
            address["city"],
            address["state"],
            address["postal_code"],
            address["country"],
            shipping_coords["lat"],
            shipping_coords["lng"],
            warehouse_id,
            total_cents,
            payment["payment_id"],
            payment["status"],
            "created",
        ),
    )
    return int(cursor.lastrowid)


def insert_order_items(
    conn,
    order_id: int,
    items: list[dict[str, int]],
    products: dict[int, dict[str, Any]],
) -> None:
    rows = [
        (
            order_id,
            item["product_id"],
            item["quantity"],
            products[item["product_id"]]["price_cents"],
        )
        for item in items
    ]

    conn.executemany(
        """
        INSERT INTO order_items (order_id, product_id, quantity, price_cents)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )


def decrement_inventory(conn, warehouse_id: int, items: list[dict[str, int]]) -> None:
    """
    Guard the inventory update with quantity >= requested quantity.

    We already checked stock before charging.
    We check again here to protect against a race with another order.
    """
    for item in items:
        cursor = conn.execute(
            """
            UPDATE inventory
            SET quantity = quantity - ?
            WHERE warehouse_id = ?
              AND product_id = ?
              AND quantity >= ?
            """,
            (
                item["quantity"],
                warehouse_id,
                item["product_id"],
                item["quantity"],
            ),
        )

        if cursor.rowcount != 1:
            # TO-DO: if payment already succeeded, trigger compensation/refund here.
            raise Conflict("inventory changed while processing the order")
