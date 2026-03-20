# Tiny Python Order API

A very small order API for an interview challenge.

## What it uses

Only the Python standard library:

- `http.server` for the HTTP server
- `sqlite3` for storage
- `urllib.request` for outbound HTTP calls to mocked external services
- `unittest` for tests

## Files

- `app.py` - HTTP server and routes
- `orders.py` - main business logic
- `db.py` - database schema, connection, seed data
- `clients.py` - mocked external HTTP clients
- `distance.py` - Haversine distance
- `tests/test_orders.py` - tests

## Run it

```bash
python app.py --db ./data/app.db
```

The server starts on `http://127.0.0.1:8000` by default.

## Create an order

```bash
curl -i \
  -X POST http://127.0.0.1:8000/orders \
  -H 'Content-Type: application/json' \
  -d '{
    "customer": {
      "name": "Coco",
      "email": "coco@example.com"
    },
    "shipping_address": {
      "line1": "123 Test St",
      "city": "New York",
      "state": "NY",
      "postal_code": "10001",
      "country": "US"
    },
    "items": [
      {"product_id": 1, "quantity": 1},
      {"product_id": 2, "quantity": 2}
    ],
    "payment": {
      "credit_card_number": "4242424242424242"
    }
  }'
```

## Mock rules

### Geocoding

The mock geocoder understands these postal codes:

- `10001` New York
- `60601` Chicago
- `94105` San Francisco
- `90001` Los Angeles

### Payments

- Any card number works **except** `4000000000000002`
- `4000000000000002` returns a payment decline

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Order creation tree of possible cases

This is the part of `create_order()` in `orders.py` that runs after validation, geocoding, pricing, and warehouse selection have already succeeded.

- The flow is intentionally split into two stages: external payment first, then internal DB writes.
- Payment happens before the DB transaction so the SQLite write lock stays short.
- The DB phase is atomic: if any write step fails, the transaction is rolled back.
- The main tradeoff is that payment can succeed before the DB write phase starts.
- That means some failure paths can lead to "customer charged, but order not saved," which is why the code explicitly mentions future compensation/refund logic.

```text
create_order()
│
├─ Payment phase
│  ├─ `charge_payment(...)` succeeds -> continue
│  ├─ payment returns 402 -> raise `PaymentDeclined`
│  ├─ payment service returns another HTTP error -> raise `ApiError`
│  └─ unexpected exception -> bubble up
│
└─ DB write phase
   ├─ open connection + `BEGIN IMMEDIATE`
   │  └─ if this fails -> rollback/re-raise
   │
   ├─ `insert_order(...)`
   │  └─ if this fails -> rollback/re-raise
   │
   ├─ `insert_order_items(...)`
   │  └─ if this fails -> rollback/re-raise
   │
   ├─ `decrement_inventory(...)`
   │  ├─ success -> continue
   │  └─ stock changed concurrently -> raise `Conflict` -> rollback/re-raise
   │
   ├─ `conn.commit()`
   │  ├─ success -> order finalized
   │  └─ failure -> rollback/re-raise
   │
   └─ finally: close DB connection
```


## Notes

- The API stores customer and shipping data directly on the order as a snapshot.
- The API does **not** store raw card numbers.
- The app calls mocked geocoding and payment endpoints over real HTTP using `urllib.request`.
- The inventory update is guarded inside a transaction to reduce overselling.
- Payments are charged before the write transaction so the database lock stays short.
- A future improvement is to add idempotency keys so retries do not create duplicate charges or duplicate orders.
- A future improvement is to add compensation/refund logic so a successful payment can be reversed if the later database transaction fails.
- Real payment/geocoding integrations are marked with `TO-DO` comments.
