# python-nostr

A Python implementation of the [Nostr protocol](https://github.com/nostr-protocol/nostr), structured as a monorepo with three packages:

- **`nostr_core`** — protocol primitives shared by the relay and client: event parsing, signature validation, filter matching
- **`nostr_relay`** — a WebSocket relay server
- **`nostr_client`** — a WebSocket client

## What is Nostr?

Nostr (Notes and Other Stuff Transmitted by Relays) is a simple, open protocol for decentralised, censorship-resistant communication. It has no central server — clients publish signed events to relays, and relays store and broadcast them to subscribers.

Every interaction in the protocol is an **event**: a JSON object signed with a secp256k1 key pair. Relays are dumb stores — they validate signatures and serve events, but interpret nothing.

## NIP compliance

| NIP                                                                | Description         | Status      |
| ------------------------------------------------------------------ | ------------------- | ----------- |
| [NIP-01](https://github.com/nostr-protocol/nips/blob/master/01.md) | Basic protocol flow | In progress |

## Project structure

```
python-nostr/
├── pyproject.toml
├── src/
│   ├── nostr_core/         # protocol primitives
│   │   ├── models.py       # Event, Filter, KindType, parse_event, parse_filter
│   │   └── validator.py    # event id and Schnorr signature verification
│   ├── nostr_relay/        # relay server
│   │   ├── connection.py   # WebSocket connection manager
│   │   ├── router.py       # protocol message dispatcher
│   │   ├── handlers/       # EVENT, REQ, CLOSE handlers
│   │   ├── subscription.py # active subscription registry
│   │   └── store.py        # SQLite event persistence
│   └── nostr_client/       # client
└── tests/
    ├── test_core/
    ├── test_relay/
    └── test_client/
```

## Architecture

```
Client connections (WebSocket)
         │
         ▼
  Connection manager       one coroutine per client, send queue per connection
         │
         ▼
   Protocol router         dispatches EVENT / REQ / CLOSE
         │
    ┌────┴────────────┐
    ▼                 ▼
EVENT handler      REQ handler       CLOSE handler
    │                 │
    ▼                 ▼
Validator     Subscription registry  in-memory, keyed by connection + sub id
    │
    ▼
EventStore                           aiosqlite, raw SQL
```

A single coroutine runs per connected client. Each connection owns a send queue — all outbound writes go through it to prevent concurrent WebSocket writes. The subscription registry holds active filters in memory; on each incoming event the relay checks it against all active subscriptions and broadcasts to matching ones.

## NIP-01 overview

NIP-01 defines the base protocol. Everything in Nostr is an event:

```json
{
  "id": "<32-byte hex SHA-256 of the serialized event>",
  "pubkey": "<32-byte hex public key>",
  "created_at": 1700000000,
  "kind": 1,
  "tags": [
    ["e", "<event-id>"],
    ["p", "<pubkey>"]
  ],
  "content": "Hello Nostr",
  "sig": "<64-byte Schnorr signature>"
}
```

The `id` is computed by the client, not assigned by the relay. The relay verifies it on every incoming event. The `sig` is a Schnorr signature over `secp256k1` — the relay verifies this too.

### Message flow

```
CLIENT → RELAY   ["EVENT", <event>]              publish an event
CLIENT → RELAY   ["REQ", "<sub_id>", <filter>]   subscribe + query stored events
CLIENT → RELAY   ["CLOSE", "<sub_id>"]           unsubscribe

RELAY → CLIENT   ["EVENT", "<sub_id>", <event>]  deliver a matching event
RELAY → CLIENT   ["EOSE",  "<sub_id>"]           end of stored events
RELAY → CLIENT   ["OK", "<event_id>", true, ""]  event accepted
RELAY → CLIENT   ["OK", "<event_id>", false, "invalid: bad signature"]
RELAY → CLIENT   ["CLOSED", "<sub_id>", ""]      subscription closed by relay
RELAY → CLIENT   ["NOTICE", "<message>"]         human-readable message
```

### Event kind ranges

| Range                                                 | Classification | Behaviour                                      |
| ----------------------------------------------------- | -------------- | ---------------------------------------------- |
| `n == 1 \| 2` or `4 <= n < 45` or `1000 <= n < 10000` | Regular        | All stored                                     |
| `n == 0 \| 3` or `10000 <= n < 20000`                 | Replaceable    | Only latest per `(pubkey, kind)` stored        |
| `20000 <= n < 30000`                                  | Ephemeral      | Not stored, only broadcast                     |
| `30000 <= n < 40000`                                  | Addressable    | Only latest per `(pubkey, kind, d-tag)` stored |
| anything else                                         | Undefined      | Relay policy applies                           |

## Development setup

Requires Python 3.10+ and [Poetry](https://python-poetry.org/docs/#installation).

```bash
git clone https://github.com/feferulo/python-nostr
cd python-nostr
uv sync
```

Poetry creates and manages the virtual environment automatically. All dependencies including dev dependencies are installed in one command.

## Running tests

```bash
uv run pytest
```

## Design decisions

**`nostr_core` as a shared library** — event parsing, signature validation, and filter matching are needed by both the relay and the client. Extracting them into a shared package avoids duplication and means protocol-level bugs are fixed in one place.

**Raw SQL over an ORM** — the relay's query performance is central to its correctness and scalability story. Writing raw SQL makes indexes, query plans, and bottlenecks visible and measurable.

**One coroutine per connection** — each WebSocket connection runs as an `asyncio` coroutine. Idle connections are cheap; the bottleneck is CPU-bound work (signature verification, broadcast fanout) not I/O wait.

**Per-connection send queue** — all outbound writes go through an `asyncio.Queue` with a dedicated writer coroutine. This serialises writes to each WebSocket and provides a natural backpressure point.
