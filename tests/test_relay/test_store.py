import pytest
from nostr_core.models import Event
from nostr_core.models import Filter
from nostr_relay.store import EventStoreProtocol, SQLiteEventStore


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def test_sqlite_event_store_satisfies_protocol():
    store = SQLiteEventStore(":memory:")
    assert isinstance(store, EventStoreProtocol)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(**overrides) -> Event:
    """Build a minimal Event with sensible defaults, overriding any fields."""
    defaults = {
        "id":         "a" * 64,
        "pubkey":     "b" * 64,
        "created_at": 1700000000,
        "kind":       1,
        "tags":       [],
        "content":    "hello nostr",
        "sig":        "c" * 128,
    }
    return Event(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def store(tmp_path):
    """Fresh in-memory EventStore, initialized and torn down per test."""
    db_path = tmp_path / "test.db"
    s = SQLiteEventStore(db_path)
    await s.initialize()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# TestInsert
# ---------------------------------------------------------------------------

class TestInsert:

    async def test_inserted_event_is_retrievable(self, store):
        event = make_event()
        await store.insert(event)
        results = await store.get_matching([Filter()])
        assert results == [event]

    async def test_duplicate_insert_is_noop(self, store):
        event = make_event()
        await store.insert(event)
        await store.insert(event)
        results = await store.get_matching([Filter()])
        assert len(results) == 1


# ---------------------------------------------------------------------------
# TestReplaceReplaceable
# ---------------------------------------------------------------------------

class TestReplaceReplaceable:

    async def test_newer_event_replaces_older(self, store):
        older = make_event(id="a" * 64, kind=0, created_at=1700000000)
        newer = make_event(id="b" * 64, kind=0, created_at=1700000001)
        await store.replace_replaceable(older)
        await store.replace_replaceable(newer)
        results = await store.get_matching([Filter(kinds=[0])])
        assert results == [newer]

    async def test_older_event_does_not_replace_newer(self, store):
        newer = make_event(id="b" * 64, kind=0, created_at=1700000001)
        older = make_event(id="a" * 64, kind=0, created_at=1700000000)
        await store.replace_replaceable(newer)
        await store.replace_replaceable(older)
        results = await store.get_matching([Filter(kinds=[0])])
        assert results == [newer]

    async def test_different_pubkeys_coexist(self, store):
        event_a = make_event(id="a" * 64, kind=0, pubkey="a" * 64)
        event_b = make_event(id="b" * 64, kind=0, pubkey="b" * 64)
        await store.replace_replaceable(event_a)
        await store.replace_replaceable(event_b)
        results = await store.get_matching([Filter(kinds=[0])])
        assert len(results) == 2

    async def test_different_kinds_coexist(self, store):
        event_a = make_event(id="a" * 64, kind=0,     pubkey="a" * 64)
        event_b = make_event(id="b" * 64, kind=10000, pubkey="a" * 64)
        await store.replace_replaceable(event_a)
        await store.replace_replaceable(event_b)
        results = await store.get_matching([Filter()])
        assert len(results) == 2


# ---------------------------------------------------------------------------
# TestReplaceAddressable
# ---------------------------------------------------------------------------

class TestReplaceAddressable:

    async def test_newer_event_replaces_older(self, store):
        older = make_event(id="a" * 64, kind=30023, created_at=1700000000,
                           tags=[["d", "my-slug"]])
        newer = make_event(id="b" * 64, kind=30023, created_at=1700000001,
                           tags=[["d", "my-slug"]])
        await store.replace_addressable(older)
        await store.replace_addressable(newer)
        results = await store.get_matching([Filter(kinds=[30023])])
        assert results == [newer]

    async def test_older_event_does_not_replace_newer(self, store):
        newer = make_event(id="b" * 64, kind=30023, created_at=1700000001,
                           tags=[["d", "my-slug"]])
        older = make_event(id="a" * 64, kind=30023, created_at=1700000000,
                           tags=[["d", "my-slug"]])
        await store.replace_addressable(newer)
        await store.replace_addressable(older)
        results = await store.get_matching([Filter(kinds=[30023])])
        assert results == [newer]

    async def test_different_d_tags_coexist(self, store):
        event_a = make_event(id="a" * 64, kind=30023, tags=[["d", "slug-a"]])
        event_b = make_event(id="b" * 64, kind=30023, tags=[["d", "slug-b"]])
        await store.replace_addressable(event_a)
        await store.replace_addressable(event_b)
        results = await store.get_matching([Filter(kinds=[30023])])
        assert len(results) == 2

    async def test_no_d_tag_defaults_to_empty_string(self, store):
        event = make_event(id="a" * 64, kind=30023, tags=[])
        await store.replace_addressable(event)
        results = await store.get_matching([Filter(kinds=[30023])])
        assert len(results) == 1

    async def test_two_events_no_d_tag_second_replaces_first(self, store):
        first  = make_event(id="a" * 64, kind=30023, created_at=1700000000, tags=[])
        second = make_event(id="b" * 64, kind=30023, created_at=1700000001, tags=[])
        await store.replace_addressable(first)
        await store.replace_addressable(second)
        results = await store.get_matching([Filter(kinds=[30023])])
        assert results == [second]


# ---------------------------------------------------------------------------
# TestGetMatching
# ---------------------------------------------------------------------------

class TestGetMatching:

    async def test_empty_filter_returns_all(self, store):
        await store.insert(make_event(id="a" * 64))
        await store.insert(make_event(id="b" * 64))
        results = await store.get_matching([Filter()])
        assert len(results) == 2

    async def test_filter_by_kind(self, store):
        await store.insert(make_event(id="a" * 64, kind=1))
        await store.insert(make_event(id="b" * 64, kind=2))
        results = await store.get_matching([Filter(kinds=[1])])
        assert results == [make_event(id="a" * 64, kind=1)]

    async def test_filter_by_author(self, store):
        await store.insert(make_event(id="a" * 64, pubkey="a" * 64))
        await store.insert(make_event(id="b" * 64, pubkey="b" * 64))
        results = await store.get_matching([Filter(authors=["a" * 64])])
        assert results == [make_event(id="a" * 64, pubkey="a" * 64)]

    async def test_filter_by_id_prefix(self, store):
        await store.insert(make_event(id="aaa" + "0" * 61))
        await store.insert(make_event(id="bbb" + "0" * 61))
        results = await store.get_matching([Filter(ids=["aaa"])])
        assert results == [make_event(id="aaa" + "0" * 61)]

    async def test_filter_since_inclusive(self, store):
        await store.insert(make_event(id="a" * 64, created_at=1700000000))
        await store.insert(make_event(id="b" * 64, created_at=1699999999))
        results = await store.get_matching([Filter(since=1700000000)])
        assert results == [make_event(id="a" * 64, created_at=1700000000)]

    async def test_filter_until_inclusive(self, store):
        await store.insert(make_event(id="a" * 64, created_at=1700000000))
        await store.insert(make_event(id="b" * 64, created_at=1700000001))
        results = await store.get_matching([Filter(until=1700000000)])
        assert results == [make_event(id="a" * 64, created_at=1700000000)]

    async def test_filter_by_e_tag(self, store):
        await store.insert(make_event(id="a" * 64, tags=[["e", "e" * 64]]))
        await store.insert(make_event(id="b" * 64, tags=[]))
        results = await store.get_matching([Filter(tags={"e": ["e" * 64]})])
        assert results == [make_event(id="a" * 64, tags=[["e", "e" * 64]])]

    async def test_filter_by_p_tag(self, store):
        await store.insert(make_event(id="a" * 64, tags=[["p", "p" * 64]]))
        await store.insert(make_event(id="b" * 64, tags=[]))
        results = await store.get_matching([Filter(tags={"p": ["p" * 64]})])
        assert results == [make_event(id="a" * 64, tags=[["p", "p" * 64]])]

    async def test_multiple_filters_or_logic(self, store):
        await store.insert(make_event(id="a" * 64, kind=1))
        await store.insert(make_event(id="b" * 64, kind=2))
        await store.insert(make_event(id="c" * 64, kind=3))
        results = await store.get_matching([Filter(kinds=[1]), Filter(kinds=[2])])
        assert len(results) == 2

    async def test_limit_is_respected(self, store):
        for i in range(5):
            await store.insert(make_event(id=str(i) * 64))
        results = await store.get_matching([Filter(limit=3)])
        assert len(results) == 3

    async def test_multi_letter_tag_not_queryable(self, store):
        await store.insert(make_event(id="a" * 64, tags=[["title", "hello"]]))
        results = await store.get_matching([Filter(tags={"title": ["hello"]})])
        assert results == []

    async def test_only_first_tag_value_is_indexed(self, store):
        event = make_event(id="a" * 64, tags=[["e", "e" * 64, "wss://relay.example.com"]])
        await store.insert(event)
        results = await store.get_matching([Filter(tags={"e": ["e" * 64]})])
        assert results == [event]

    async def test_results_ordered_by_created_at_descending(self, store):
        await store.insert(make_event(id="a" * 64, created_at=1700000001))
        await store.insert(make_event(id="b" * 64, created_at=1700000000))
        await store.insert(make_event(id="c" * 64, created_at=1700000002))
        results = await store.get_matching([Filter()])
        assert results[0].created_at == 1700000002
        assert results[1].created_at == 1700000001
        assert results[2].created_at == 1700000000
