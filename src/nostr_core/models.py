import hashlib
import json
from dataclasses import dataclass
from enum import Enum, auto


# ---------------------------------------------------------------------------
# KindType
# ---------------------------------------------------------------------------

class KindType(Enum):
    REGULAR     = auto()
    REPLACEABLE = auto()
    EPHEMERAL   = auto()
    ADDRESSABLE = auto()
    UNDEFINED   = auto()


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

@dataclass
class Event:
    id:         str
    pubkey:     str
    created_at: int
    kind:       int
    tags:       list[list[str]]
    content:    str
    sig:        str

    @property
    def kind_type(self) -> KindType:
        k = self.kind
        if k in (0, 3) or 10000 <= k < 20000:
            return KindType.REPLACEABLE
        if 20000 <= k < 30000:
            return KindType.EPHEMERAL
        if 30000 <= k < 40000:
            return KindType.ADDRESSABLE
        if k in (1, 2) or 4 <= k < 45 or 1000 <= k < 10000:
            return KindType.REGULAR
        return KindType.UNDEFINED

    def serialize(self) -> list:
        """Canonical serialization for event id computation per NIP-01."""
        return [0, self.pubkey, self.created_at, self.kind, self.tags, self.content]

    def compute_id(self) -> str:
        """SHA-256 of the UTF-8 encoded canonical serialization."""
        serialized = json.dumps(
            self.serialize(),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

@dataclass
class Filter:
    ids:     list[str] | None = None
    authors: list[str] | None = None
    kinds:   list[int] | None = None
    since:   int | None       = None
    until:   int | None       = None
    limit:   int | None       = None
    tags:    dict[str, list[str]] | None = None

    def matches(self, event: Event) -> bool:
        """Return True if the event satisfies all conditions in this filter."""
        if self.kinds is not None and event.kind not in self.kinds:
            return False
        if self.since is not None and event.created_at < self.since:
            return False
        if self.until is not None and event.created_at > self.until:
            return False
        if self.ids is not None and not any(event.id.startswith(id_) for id_ in self.ids):
            return False
        if self.authors is not None and not any(event.pubkey.startswith(a) for a in self.authors):
            return False
        if self.tags is not None:
            event_tags = {t[0]: t[1:] for t in event.tags if len(t) >= 2}
            for tag_name, wanted_values in self.tags.items():
                event_values = event_tags.get(tag_name, [])
                if not any(v in event_values for v in wanted_values):
                    return False
        return True


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_event(data: dict) -> Event:
    """
    Parse a raw dict into an Event, validating shape and field types.
    Does NOT verify the signature or recompute the id — that is the
    validator's responsibility.

    Raises ValueError with the offending field name in the message.
    """
    required_str_fields = ["id", "pubkey", "sig", "content"]
    required_int_fields = ["created_at", "kind"]

    for field in required_str_fields:
        if field not in data:
            raise ValueError(f"missing field: {field}")
        if not isinstance(data[field], str):
            raise ValueError(f"invalid type for field: {field}")

    for field in required_int_fields:
        if field not in data:
            raise ValueError(f"missing field: {field}")
        if not isinstance(data[field], int):
            raise ValueError(f"invalid type for field: {field}")

    if "tags" not in data:
        raise ValueError("missing field: tags")
    if not isinstance(data["tags"], list):
        raise ValueError("invalid type for field: tags")

    if len(data["id"]) != 64:
        raise ValueError("invalid length for field: id")
    if len(data["pubkey"]) != 64:
        raise ValueError("invalid length for field: pubkey")
    if len(data["sig"]) != 128:
        raise ValueError("invalid length for field: sig")

    return Event(
        id=data["id"],
        pubkey=data["pubkey"],
        created_at=data["created_at"],
        kind=data["kind"],
        tags=data["tags"],
        content=data["content"],
        sig=data["sig"],
    )


def parse_filter(data: dict) -> Filter:
    """
    Parse a raw dict into a Filter. All fields are optional.
    Unknown fields are silently ignored per the spec.
    Tag filter keys (e.g. '#e') have their leading '#' stripped.
    """
    tags = None
    tag_filters = {
        k[1:]: v for k, v in data.items()
        if k.startswith("#") and isinstance(v, list)
    }
    if tag_filters:
        tags = tag_filters

    return Filter(
        ids=data.get("ids"),
        authors=data.get("authors"),
        kinds=data.get("kinds"),
        since=data.get("since"),
        until=data.get("until"),
        limit=data.get("limit"),
        tags=tags,
    )
