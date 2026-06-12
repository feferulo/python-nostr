import asyncio
import json
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

import aiosqlite

from nostr_core.models import Event, Filter


SINGLE_LETTER = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


@runtime_checkable
class EventStoreProtocol(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def insert(self, event: Event) -> None: ...
    async def replace_replaceable(self, event: Event) -> None: ...
    async def replace_addressable(self, event: Event) -> None: ...
    async def get_matching(self, filters: list[Filter]) -> list[Event]: ...


class SQLiteEventStore:

    def __init__(self, db_path):
        self.db_path = db_path
        self.db = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id         TEXT PRIMARY KEY,
                pubkey     TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                kind       INTEGER NOT NULL,
                tags       TEXT NOT NULL,
                content    TEXT NOT NULL,
                sig        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tags (
                event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                name     TEXT NOT NULL,
                value    TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_pubkey
                ON events(pubkey);
            CREATE INDEX IF NOT EXISTS idx_events_kind
                ON events(kind);
            CREATE INDEX IF NOT EXISTS idx_events_created_at
                ON events(created_at);
            CREATE INDEX IF NOT EXISTS idx_tags
                ON tags(name, value);
        """)
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    # -----------------------------------------------------------------------
    # Transaction context manager
    # -----------------------------------------------------------------------

    @asynccontextmanager
    async def _transaction(self):
        """Group multiple execute calls into a single atomic operation."""
        async with self._lock:
            await self.db.execute("BEGIN")
            try:
                yield
                await self.db.commit()
            except Exception as e:
                await self.db.rollback()
                raise RuntimeError(f"transaction failed and was rolled back: {e}") from e

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _row_to_event(self, row) -> Event:
        return Event(
            id=row[0],
            pubkey=row[1],
            created_at=row[2],
            kind=row[3],
            tags=json.loads(row[4]),
            content=row[5],
            sig=row[6],
        )

    async def _insert_tags(self, event: Event) -> None:
        """Index single-letter tags, first value only, per NIP-01."""
        rows = [
            (event.id, tag[0], tag[1])
            for tag in event.tags
            if len(tag) >= 2
            and len(tag[0]) == 1
            and tag[0] in SINGLE_LETTER
        ]
        if rows:
            await self.db.executemany(
                "INSERT INTO tags(event_id, name, value) VALUES (?, ?, ?)",
                rows
            )

    async def _insert_event(self, event: Event) -> None:
        """Insert event row."""
        await self.db.execute(
            """INSERT INTO events(id, pubkey, created_at, kind, tags, content, sig)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event.id, event.pubkey, event.created_at, event.kind,
             json.dumps(event.tags, separators=(",", ":"), ensure_ascii=False),
             event.content, event.sig)
        )

    async def _insert(self, event: Event) -> None:
        """Insert event row and its tags."""
        await self._insert_event(event)
        await self._insert_tags(event)

    # -----------------------------------------------------------------------
    # Write operations
    # -----------------------------------------------------------------------

    async def insert(self, event: Event) -> None:
        """Insert a regular event. Silently ignores duplicate ids."""
        async with self.db.execute(
            "SELECT 1 FROM events WHERE id = ?", (event.id,)
        ) as cursor:
            if await cursor.fetchone():
                return

        async with self._transaction():
            await self._insert(event)

    async def replace_replaceable(self, event: Event) -> None:
        """
        Atomically replace the stored event for (pubkey, kind) with the
        incoming event, but only if the incoming event is newer.
        """
        async with self.db.execute(
            "SELECT created_at FROM events WHERE pubkey = ? AND kind = ?",
            (event.pubkey, event.kind)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] >= event.created_at:
                return

        async with self._transaction():
            await self.db.execute(
                "DELETE FROM events WHERE pubkey = ? AND kind = ?",
                (event.pubkey, event.kind)
            )
            await self._insert(event)

    async def replace_addressable(self, event: Event) -> None:
        """
        Atomically replace the stored event for (pubkey, kind, d_tag) with
        the incoming event, but only if the incoming event is newer.
        The d_tag defaults to "" if no d tag is present on the event.
        """
        d_tag = next(
            (t[1] for t in event.tags if t[0] == "d" and len(t) > 1),
            ""
        )

        async with self.db.execute(
            """SELECT e.created_at FROM events e
               LEFT JOIN tags t ON t.event_id = e.id AND t.name = 'd'
               WHERE e.pubkey = ? AND e.kind = ?
               AND COALESCE(t.value, '') = ?""",
            (event.pubkey, event.kind, d_tag)
        ) as cursor:
            row = await cursor.fetchone()

        if row and row[0] >= event.created_at:
            return

        async with self._transaction():
            await self.db.execute(
                """DELETE FROM events WHERE pubkey = ? AND kind = ? AND id IN (
                    SELECT e.id FROM events e
                    LEFT JOIN tags t ON t.event_id = e.id AND t.name = 'd'
                    WHERE e.pubkey = ? AND e.kind = ?
                    AND COALESCE(t.value, '') = ?
                )""",
                (event.pubkey, event.kind, event.pubkey, event.kind, d_tag)
            )
            await self._insert(event)

    # -----------------------------------------------------------------------
    # Read operations
    # -----------------------------------------------------------------------

    async def get_matching(self, filters: list[Filter]) -> list[Event]:
        """
        Return all stored events matching any of the given filters (OR logic).
        Results are ordered by created_at descending.
        The limit of the first filter that specifies one is respected.
        """
        if not filters:
            return []

        all_ids: set[str] = set()
        limit = next((f.limit for f in filters if f.limit is not None), None)

        for f in filters:
            ids = await self._query_filter(f)
            all_ids.update(ids)

        if not all_ids:
            return []

        placeholders = ",".join("?" * len(all_ids))
        query = f"""
            SELECT id, pubkey, created_at, kind, tags, content, sig
            FROM events
            WHERE id IN ({placeholders})
            ORDER BY created_at DESC
        """
        params = list(all_ids)

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        async with self.db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        return [self._row_to_event(row) for row in rows]

    async def _query_filter(self, f: Filter) -> list[str]:
        """Return event ids matching a single filter."""
        conditions = []
        params = []

        if f.kinds is not None:
            placeholders = ",".join("?" * len(f.kinds))
            conditions.append(f"e.kind IN ({placeholders})")
            params.extend(f.kinds)

        if f.authors is not None:
            author_conditions = " OR ".join("e.pubkey LIKE ?" for _ in f.authors)
            conditions.append(f"({author_conditions})")
            params.extend(f"{a}%" for a in f.authors)

        if f.ids is not None:
            id_conditions = " OR ".join("e.id LIKE ?" for _ in f.ids)
            conditions.append(f"({id_conditions})")
            params.extend(f"{i}%" for i in f.ids)

        if f.since is not None:
            conditions.append("e.created_at >= ?")
            params.append(f.since)

        if f.until is not None:
            conditions.append("e.created_at <= ?")
            params.append(f.until)

        if f.tags:
            for i, (name, values) in enumerate(f.tags.items()):
                if len(name) != 1 or name not in SINGLE_LETTER:
                    return []
                alias = f"t{i}"
                placeholders = ",".join("?" * len(values))
                conditions.append(
                    f"EXISTS (SELECT 1 FROM tags {alias} "
                    f"WHERE {alias}.event_id = e.id "
                    f"AND {alias}.name = ? "
                    f"AND {alias}.value IN ({placeholders}))"
                )
                params.append(name)
                params.extend(values)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT e.id FROM events e {where}"

        async with self.db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        return [row[0] for row in rows]
