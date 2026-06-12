# EventStore Design

This document describes the design decisions behind `nostr_relay/store.py`.

## Schema

The store uses two tables:

```sql
CREATE TABLE events (
    id         TEXT PRIMARY KEY,
    pubkey     TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    kind       INTEGER NOT NULL,
    tags       TEXT NOT NULL,    -- full tags array as a JSON blob
    content    TEXT NOT NULL,
    sig        TEXT NOT NULL
);

CREATE TABLE tags (
    event_id   TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,    -- single-letter tag name, e.g. "e", "p"
    value      TEXT NOT NULL     -- first value of the tag only
);

CREATE INDEX idx_tags               ON tags(name, value);
CREATE INDEX idx_events_pubkey      ON events(pubkey);
CREATE INDEX idx_events_kind        ON events(kind);
CREATE INDEX idx_events_created_at  ON events(created_at);
```

### Index rationale

Every filter key in NIP-01 is a legitimate standalone query condition — a
client can send a filter with only `authors`, only `kinds`, or only `since`.
Each filter key therefore deserves its own index:

- `idx_events_id` — implicit, covered by `PRIMARY KEY` on `events(id)`
- `idx_events_pubkey` — `authors` filter: `WHERE pubkey IN (...)`
- `idx_events_kind` — `kinds` filter: `WHERE kind IN (...)`
- `idx_events_created_at` — `since`/`until` filters and `ORDER BY created_at DESC` on every query
- `idx_tags` — `#x` tag filters: JOIN on `tags(name, value)`

### Why a separate tags table?

Filter queries like `{"#e": ["abc123"]}` require finding all events that have
an `e` tag with value `"abc123"`. Without a separate table this would require
scanning every row and parsing the tags JSON blob — slow at scale.

The tags table is essentially a pre-built index: given a `(name, value)` pair,
the database can jump directly to matching event ids via `idx_tags`.

### What gets indexed

NIP-01 specifies that only single-letter tags (a-z, A-Z) are expected to be
indexed by relays. This means:

- `["e", "abc123"]` → indexed (single-letter name, `e`)
- `["p", "abc123"]` → indexed (single-letter name, `p`)
- `["title", "My Article"]` → NOT indexed (multi-letter name)

Multi-letter tags are still stored in the events table's `tags` JSON blob and
returned as part of the event — they just cannot be queried via `#x` filters.

### Only the first tag value is indexed

For a tag like `["e", "abc123", "wss://relay.example.com", "reply"]`, only
`"abc123"` (position 1) is indexed. The relay URL and marker at positions 2+
are not indexed, per the NIP-01 spec.

## SQLite configuration

Three PRAGMAs are set on connection startup:

- **`journal_mode=WAL`** — Write-Ahead Logging allows concurrent reads during a write. Without it SQLite locks the entire database on every write, serializing all requests.
- **`foreign_keys=ON`** — SQLite doesn't enforce foreign keys by default. Required for `ON DELETE CASCADE` on the tags table to work correctly.
- **`busy_timeout=5000`** — If the database is locked (e.g. by an external tool or process), SQLite retries for up to 5 seconds before raising an error. The asyncio lock prevents contention between coroutines in normal operation; this is a safety net for external access.
- **`synchronous=NORMAL`** — In WAL mode, `NORMAL` flushes at less critical moments than the default `FULL`. The SQLite docs explicitly recommend this combination for a good balance of safety and performance. The tradeoff: committed transactions could be lost on an OS crash (power failure), but the database will never be corrupted. For a Nostr relay this is acceptable — losing the last few events on a power failure is far less serious than corrupting the event store.

## Atomicity of replacements

Replaceable and addressable events require deleting the older event and
inserting the new one as a single atomic operation. If these were separate
operations, a concurrent read between the delete and insert could see a missing
event.

The store owns this atomicity — both operations run inside a single SQLite
transaction. The handler decides *which* replacement method to call based on
`kind_type`, but the store guarantees the operation is atomic.

## Replaceable vs addressable replacement

These are two separate methods because they delete on different keys:

- `replace_replaceable(event)` — deletes by `(pubkey, kind)`
- `replace_addressable(event)` — deletes by `(pubkey, kind, d_tag)`

where `d_tag` is extracted from the event's `d` tag value, defaulting to `""`
if absent.

Merging them into one method would push `KindType` logic into the store, which
should stay unaware of event classification policy.

## Stale event protection

If a newer replaceable event is already stored and an older one arrives (e.g.
due to network reordering), the store discards the incoming event rather than
replacing the newer one. The check is:

```
if stored.created_at >= incoming.created_at → discard incoming
```

This applies to both `replace_replaceable` and `replace_addressable`.

## Querying

`get_matching(filters)` takes a list of `Filter` objects. Multiple filters are
OR logic — an event is returned if it matches any filter. Each filter's
conditions are AND logic internally.

Results are always ordered by `created_at` descending (newest first), which is
the standard expectation for Nostr clients. The `limit` field of the first
matching filter is respected.

## Tag filter query strategy

Tag filter queries use a JOIN against the tags table:

```sql
SELECT e.* FROM events e
JOIN tags t ON t.event_id = e.id
WHERE t.name = ? AND t.value IN (?, ?, ...)
```

This is efficient because `idx_tags` on `(name, value)` makes the lookup fast.

## Database portability

The store is implemented against an `EventStore` protocol (abstract interface).
The concrete implementation uses `aiosqlite`. Switching to PostgreSQL would
require a new implementation of the same interface — the upsert syntax differs
(`INSERT OR IGNORE` vs `INSERT ... ON CONFLICT DO NOTHING`) but the schema and
query logic are standard SQL.
