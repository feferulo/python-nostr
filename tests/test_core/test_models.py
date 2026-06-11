import json
import pytest
from nostr_core.models import Event, KindType, Filter, parse_event, parse_filter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_event_dict():
    """Minimal structurally valid raw event dict. Crypto is fake."""
    return {
        "id":         "a" * 64,
        "pubkey":     "b" * 64,
        "created_at": 1700000000,
        "kind":       1,
        "tags":       [],
        "content":    "hello nostr",
        "sig":        "c" * 128,
    }


@pytest.fixture
def make_event(valid_event_dict):
    """Fixture factory — returns a fresh Event with any fields overridden."""
    def _make(**overrides):
        return Event(**{**valid_event_dict, **overrides})
    return _make


@pytest.fixture
def make_event_dict(valid_event_dict):
    """Fixture factory — returns a fresh dict with any fields overridden.
    Use this for parse_event tests to avoid mutating the base fixture."""
    def _make(**overrides):
        return {**valid_event_dict, **overrides}
    return _make


# ---------------------------------------------------------------------------
# Event — kind classification via kind_type property
# ---------------------------------------------------------------------------

class TestKindType:
    """
    Spec-defined ranges (current NIP-01):
      regular:     1000 <= n < 10000 || 4 <= n < 45 || n == 1 || n == 2
      replaceable: 10000 <= n < 20000 || n == 0 || n == 3
      ephemeral:   20000 <= n < 30000
      addressable: 30000 <= n < 40000  (identified by kind + pubkey + d tag)
      undefined:   anything outside all ranges above
    """

    # --- Regular ---

    @pytest.mark.parametrize("kind", [1, 2])
    def test_regular_explicit_kinds(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.REGULAR

    @pytest.mark.parametrize("kind", [4, 44])
    def test_regular_4_to_44_boundaries(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.REGULAR

    @pytest.mark.parametrize("kind", [1000, 9999])
    def test_regular_1000_to_9999_boundaries(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.REGULAR

    # --- Replaceable ---

    @pytest.mark.parametrize("kind", [0, 3])
    def test_replaceable_explicit_kinds(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.REPLACEABLE

    @pytest.mark.parametrize("kind", [10000, 19999])
    def test_replaceable_range_boundaries(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.REPLACEABLE

    # --- Ephemeral ---

    @pytest.mark.parametrize("kind", [20000, 29999])
    def test_ephemeral_range_boundaries(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.EPHEMERAL

    # --- Addressable ---

    @pytest.mark.parametrize("kind", [30000, 39999])
    def test_addressable_range_boundaries(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.ADDRESSABLE

    # --- Undefined ---

    @pytest.mark.parametrize("kind", [45, 999, 40000])
    def test_undefined_kinds(self, make_event, kind):
        assert make_event(kind=kind).kind_type == KindType.UNDEFINED

    # --- Range boundary crossings ---
    # Each pair tests the last kind IN a range and the first kind OUT of it.

    @pytest.mark.parametrize("kind,expected", [
        (3,     KindType.REPLACEABLE),  # last explicit replaceable
        (4,     KindType.REGULAR),      # first of 4..44 regular range
        (44,    KindType.REGULAR),      # last of 4..44 regular range
        (45,    KindType.UNDEFINED),    # first undefined
        (999,   KindType.UNDEFINED),    # just before 1000..9999 regular range
        (1000,  KindType.REGULAR),      # first of 1000..9999 regular range
        (9999,  KindType.REGULAR),      # last of 1000..9999 regular range
        (10000, KindType.REPLACEABLE),  # first of replaceable range
        (19999, KindType.REPLACEABLE),  # last of replaceable range
        (20000, KindType.EPHEMERAL),    # first of ephemeral range
        (29999, KindType.EPHEMERAL),    # last of ephemeral range
        (30000, KindType.ADDRESSABLE),  # first of addressable range
        (39999, KindType.ADDRESSABLE),  # last of addressable range
        (40000, KindType.UNDEFINED),    # first kind above all ranges
    ])
    def test_range_boundaries(self, make_event, kind, expected):
        assert make_event(kind=kind).kind_type == expected


# ---------------------------------------------------------------------------
# Event — serialization and id computation
# ---------------------------------------------------------------------------

class TestSerialization:

    def test_serialize_is_list(self, make_event):
        assert isinstance(make_event().serialize(), list)

    def test_serialize_starts_with_zero(self, make_event):
        assert make_event().serialize()[0] == 0

    def test_serialize_field_order(self, make_event):
        event = make_event()
        s = event.serialize()
        # [0, pubkey, created_at, kind, tags, content] — id and sig excluded
        assert s == [0, event.pubkey, event.created_at, event.kind, event.tags, event.content]

    def test_serialize_json_has_no_whitespace(self, make_event):
        # use content with no spaces so we can assert the JSON structure itself
        # has no whitespace separators — not just that the content is space-free
        serialized_json = json.dumps(
            make_event(content="nostr").serialize(),
            separators=(",", ":"),
            ensure_ascii=False
        )
        assert " " not in serialized_json

    def test_compute_id_is_64_char_hex(self, make_event):
        computed = make_event().compute_id()
        assert len(computed) == 64
        assert all(c in "0123456789abcdef" for c in computed)

    def test_compute_id_matches_expected(self, make_event):
        # pre-computed from valid_event_dict canonical serialization:
        # [0,"bbbb...bbbb",1700000000,1,[],"hello nostr"]
        # $ echo -n '[0,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",1700000000,1,[],"hello nostr"]' | sha256sum
        assert make_event().compute_id() == "ca7448d8d4655415d1b0fef59dab62b924c422875e5bbebb76ab6883683e5102"

    def test_compute_id_changes_with_content(self, make_event):
        assert make_event(content="hello").compute_id() != make_event(content="world").compute_id()

    # --- Content escaping (NIP-01 spec-mandated escape rules) ---
    # Python's json.dumps handles these, but we verify explicitly because
    # getting this wrong produces a different event id and breaks interop.

    @pytest.mark.parametrize("char,expected_escape", [
        ("\n",   "\\n"),
        ("\r",   "\\r"),
        ("\t",   "\\t"),
        ("\"",   "\\\""),
        ("\\",   "\\\\"),
        ("\x08", "\\b"),
        ("\x0c", "\\f"),
    ])
    def test_content_special_chars_are_escaped(self, make_event, char, expected_escape):
        serialized_json = json.dumps(
            make_event(content=char).serialize(),
            separators=(",", ":"),
            ensure_ascii=False
        )
        assert expected_escape in serialized_json

    def test_compute_id_differs_for_escaped_vs_literal(self, make_event):
        # "\n" and "\\n" are different content strings — must produce different ids
        assert make_event(content="\n").compute_id() != make_event(content="\\n").compute_id()


# ---------------------------------------------------------------------------
# parse_event — happy path
# ---------------------------------------------------------------------------

class TestParseEventHappyPath:

    def test_returns_event_instance(self, make_event_dict):
        assert isinstance(parse_event(make_event_dict()), Event)

    def test_fields_mapped_correctly(self, make_event_dict):
        data = make_event_dict()
        event = parse_event(data)
        assert event.id == data["id"]
        assert event.pubkey == data["pubkey"]
        assert event.created_at == data["created_at"]
        assert event.kind == data["kind"]
        assert event.tags == data["tags"]
        assert event.content == data["content"]
        assert event.sig == data["sig"]

    def test_with_non_empty_tags(self, make_event_dict):
        event = parse_event(make_event_dict(tags=[["e", "a" * 64], ["p", "b" * 64]]))
        assert len(event.tags) == 2

    def test_extra_fields_ignored(self, make_event_dict):
        assert isinstance(parse_event(make_event_dict(unknown_field="ignored")), Event)


# ---------------------------------------------------------------------------
# parse_event — missing required fields (parametrized)
# ---------------------------------------------------------------------------

class TestParseEventMissingFields:

    @pytest.mark.parametrize("field", [
        "id", "pubkey", "created_at", "kind", "tags", "content", "sig"
    ])
    def test_missing_required_field_raises(self, make_event_dict, field):
        data = make_event_dict()
        del data[field]
        with pytest.raises(ValueError, match=field):
            parse_event(data)


# ---------------------------------------------------------------------------
# parse_event — wrong types (parametrized)
# ---------------------------------------------------------------------------

class TestParseEventWrongTypes:

    @pytest.mark.parametrize("field,bad_value", [
        ("id",         12345),
        ("pubkey",     12345),
        ("created_at", "not-an-int"),
        ("kind",       "1"),
        ("tags",       "not-a-list"),
        ("content",    12345),
        ("sig",        12345),
    ])
    def test_wrong_type_raises(self, make_event_dict, field, bad_value):
        with pytest.raises(ValueError):
            parse_event(make_event_dict(**{field: bad_value}))


# ---------------------------------------------------------------------------
# parse_event — invalid field lengths (parametrized)
# ---------------------------------------------------------------------------

class TestParseEventInvalidLengths:

    @pytest.mark.parametrize("field,bad_value", [
        ("id",     "a" * 63),   # too short
        ("id",     "a" * 65),   # too long
        ("pubkey", "b" * 63),
        ("pubkey", "b" * 65),
        ("sig",    "c" * 127),
        ("sig",    "c" * 129),
    ])
    def test_invalid_length_raises(self, make_event_dict, field, bad_value):
        with pytest.raises(ValueError):
            parse_event(make_event_dict(**{field: bad_value}))


# ---------------------------------------------------------------------------
# parse_filter — happy path
# ---------------------------------------------------------------------------

class TestParseFilterHappyPath:

    def test_empty_dict_is_valid(self):
        assert isinstance(parse_filter({}), Filter)

    def test_all_none_on_empty_dict(self):
        f = parse_filter({})
        assert f.ids is None
        assert f.authors is None
        assert f.kinds is None
        assert f.since is None
        assert f.until is None
        assert f.limit is None
        assert f.tags is None

    def test_all_fields_parsed(self):
        f = parse_filter({
            "ids":     ["a" * 64],
            "authors": ["b" * 64],
            "kinds":   [0, 1],
            "since":   1700000000,
            "until":   1700099999,
            "limit":   20,
        })
        assert f.kinds == [0, 1]
        assert f.since == 1700000000
        assert f.limit == 20

    def test_tag_key_hash_stripped(self):
        f = parse_filter({"#e": ["a" * 64]})
        assert f.tags == {"e": ["a" * 64]}

    def test_multiple_tag_filters(self):
        f = parse_filter({"#e": ["a" * 64], "#p": ["b" * 64]})
        assert "e" in f.tags
        assert "p" in f.tags

    def test_unknown_fields_ignored(self):
        f = parse_filter({"kinds": [1], "future_nip_field": "whatever"})
        assert f.kinds == [1]


# ---------------------------------------------------------------------------
# Filter.matches
# ---------------------------------------------------------------------------

class TestFilterMatches:

    def test_empty_filter_matches_everything(self, make_event):
        assert Filter().matches(make_event())

    def test_kind_match(self, make_event):
        assert Filter(kinds=[1]).matches(make_event(kind=1))

    def test_kind_no_match(self, make_event):
        assert not Filter(kinds=[0, 2]).matches(make_event(kind=1))

    def test_since_inclusive_boundary(self, make_event):
        # created_at == since should match (spec: >=)
        assert Filter(since=1700000000).matches(make_event(created_at=1700000000))

    def test_since_excludes_older(self, make_event):
        assert not Filter(since=1700000001).matches(make_event(created_at=1700000000))

    def test_until_inclusive_boundary(self, make_event):
        # created_at == until should match (spec: <=)
        assert Filter(until=1700000000).matches(make_event(created_at=1700000000))

    def test_until_excludes_newer(self, make_event):
        assert not Filter(until=1699999999).matches(make_event(created_at=1700000000))

    def test_author_exact_match(self, make_event):
        assert Filter(authors=["b" * 64]).matches(make_event(pubkey="b" * 64))

    def test_author_prefix_match(self, make_event):
        assert Filter(authors=["b" * 10]).matches(make_event(pubkey="b" * 64))

    def test_author_no_match(self, make_event):
        assert not Filter(authors=["c" * 64]).matches(make_event(pubkey="b" * 64))

    def test_id_prefix_match(self, make_event):
        assert Filter(ids=["a" * 10]).matches(make_event(id="a" * 64))

    def test_id_no_match(self, make_event):
        assert not Filter(ids=["b" * 64]).matches(make_event(id="a" * 64))

    def test_tag_filter_match(self, make_event):
        assert Filter(tags={"e": ["a" * 64]}).matches(make_event(tags=[["e", "a" * 64]]))

    def test_tag_filter_no_match(self, make_event):
        assert not Filter(tags={"e": ["b" * 64]}).matches(make_event(tags=[["e", "a" * 64]]))

    def test_multiple_conditions_all_must_pass(self, make_event):
        # kind matches but since does not
        assert not Filter(kinds=[1], since=1700000001).matches(make_event(kind=1, created_at=1700000000))

