from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
);

CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    warehouse_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (warehouse_id, product_id),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    shipping_line1 TEXT NOT NULL,
    shipping_city TEXT NOT NULL,
    shipping_state TEXT NOT NULL,
    shipping_postal_code TEXT NOT NULL,
    shipping_country TEXT NOT NULL,
    shipping_lat REAL NOT NULL,
    shipping_lng REAL NOT NULL,
    warehouse_id INTEGER NOT NULL,
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    payment_id TEXT NOT NULL,
    payment_status TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    PRIMARY KEY (order_id, product_id),
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
"""


def connect_db(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with safe defaults for this tiny app."""
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row # makes rows behave like dict-like objects

    # SQLite does NOT enforce foreign keys unless you enable it per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    """Create tables if they do not exist yet."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = connect_db(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def seed_demo_data(db_path: str) -> None:
    """Seed a tiny, deterministic dataset so the API works out of the box."""
    conn = connect_db(db_path)
    try:
        product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if product_count > 0:
            return

        conn.executemany(
            "INSERT INTO products (id, sku, name, price_cents) VALUES (?, ?, ?, ?)",
            [
                (1, "WIDGET", "Widget", 1500),
                (2, "CABLE", "Cable", 500),
                (3, "MOUSE", "Mouse", 2500),
            ],
        )

        conn.executemany(
            "INSERT INTO warehouses (id, name, lat, lng) VALUES (?, ?, ?, ?)",
            [
                (1, "East Warehouse", 40.7128, -74.0060),
                (2, "Midwest Warehouse", 41.8781, -87.6298),
                (3, "West Warehouse", 34.0522, -118.2437),
            ],
        )

        conn.executemany(
            "INSERT INTO inventory (warehouse_id, product_id, quantity) VALUES (?, ?, ?)",
            [
                # East can fulfill product 1 + 2, but not 3.
                (1, 1, 10),
                (1, 2, 10),
                (1, 3, 0),
                # Midwest can fulfill all three products.
                (2, 1, 10),
                (2, 2, 10),
                (2, 3, 10),
                # West cannot fulfill product 2.
                (3, 1, 10),
                (3, 2, 0),
                (3, 3, 10),
            ],
        )

        conn.commit()
    finally:
        conn.close()
