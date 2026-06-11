import coincurve

from nostr_core.models import Event


def verify_id(event: Event) -> None:
    """
    Recompute the event id from its canonical serialization and verify
    it matches the id field.

    Raises ValueError if the id does not match.
    """
    computed = event.compute_id()
    if computed != event.id:
        raise ValueError(f"id mismatch (expected {computed}, got {event.id})")


def verify_signature(event: Event) -> None:
    """
    Verify the Schnorr signature over the event id using the event pubkey.

    Raises ValueError if the signature is invalid.
    """
    try:
        public_key = coincurve.PublicKeyXOnly(bytes.fromhex(event.pubkey))
        valid = public_key.verify(
            signature=bytes.fromhex(event.sig),
            message=bytes.fromhex(event.id),
        )
    except Exception as e:
        raise ValueError(f"bad signature ({e})")

    if not valid:
        raise ValueError("bad signature")


def validate_event(event: Event) -> None:
    """
    Fully validate an event — id integrity first, then signature.
    The id is checked first so that id errors always take precedence.

    Raises ValueError on the first failure.
    """
    verify_id(event)
    verify_signature(event)
