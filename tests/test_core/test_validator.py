import pytest
from nostr_core.models import Event
from nostr_core.validator import validate_event, verify_id, verify_signature


# ---------------------------------------------------------------------------
# Constants — real cryptographically valid event generated once externally.
# Private key retained so tampered variants can be reasoned about precisely.
#
# Generated with:
#   private_key = coincurve.PrivateKey(os.urandom(32))
#   serialized  = json.dumps([0, pubkey, created_at, kind, tags, content], ...)
#   id          = sha256(serialized)
#   sig         = private_key.sign_schnorr(bytes.fromhex(id))
# ---------------------------------------------------------------------------

PRIVATE_KEY = "7dde868684c65e37ee54f1c5c8d1f933f9e0a601ab577152802065a43ee4c5b7"

VALID_EVENT = {
    "id":         "f4e147ec4c52bf06f8dbdf6d7e12d88a081b52ff536c19331c8a1be7be9a2555",
    "pubkey":     "623c1e7dc6367a99ba8568e89d857be94b18f41bd8f7531be21d07b66cc2d549",
    "created_at": 1700000000,
    "kind":       1,
    "tags":       [],
    "content":    "hello nostr",
    "sig":        "8a12bd24b1b3c797d740da8915def6b1e2bdbc2fc6494e2d146c6d77f9f89523d0207ef46256acc843ca91a617ea7983c953830afd1643a59518aac07f2b86f4",
}

# Substituted values for tampered event tests
OTHER_PUBKEY = "a" * 64
BAD_ID     = "b" * 64
BAD_SIG      = VALID_EVENT["sig"][:-1] + "0"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_event():
    """A real cryptographically valid Event."""
    return Event(**VALID_EVENT)


@pytest.fixture
def make_event():
    """Fixture factory — returns a fresh Event with any fields overridden."""
    def _make(**overrides):
        return Event(**{**VALID_EVENT, **overrides})
    return _make


# ---------------------------------------------------------------------------
# verify_id
# ---------------------------------------------------------------------------

class TestVerifyId:

    def test_valid_event_passes(self, valid_event):
        verify_id(valid_event)  # must not raise

    def test_tampered_content_raises(self, make_event):
        # content changed — recomputed id will differ from event.id
        with pytest.raises(ValueError, match="id mismatch"):
            verify_id(make_event(content="tampered"))

    def test_tampered_id_raises(self, make_event):
        # id replaced directly — doesn't match recomputed hash of unchanged data
        with pytest.raises(ValueError, match="id mismatch"):
            verify_id(make_event(id=BAD_ID))


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

class TestVerifySignature:

    def test_valid_event_passes(self, valid_event):
        verify_signature(valid_event)  # must not raise

    def test_tampered_sig_raises(self, make_event):
        # flip last character of sig
        with pytest.raises(ValueError, match="bad signature"):
            verify_signature(make_event(sig=BAD_SIG))

    def test_wrong_pubkey_raises(self, make_event):
        # sig was made by PRIVATE_KEY, not by OTHER_PUBKEY's key
        with pytest.raises(ValueError, match="bad signature"):
            verify_signature(make_event(pubkey=OTHER_PUBKEY))


# ---------------------------------------------------------------------------
# validate_event
# ---------------------------------------------------------------------------

class TestValidateEvent:

    def test_valid_event_passes(self, valid_event):
        validate_event(valid_event)  # must not raise

    def test_bad_id_raises_id_error(self, make_event):
        with pytest.raises(ValueError, match="id mismatch"):
            validate_event(make_event(id=BAD_ID))

    def test_bad_sig_raises_sig_error(self, make_event):
        with pytest.raises(ValueError, match="bad signature"):
            validate_event(make_event(sig=BAD_SIG))

    def test_id_error_takes_precedence_over_sig_error(self, make_event):
        # id and sig are both wrong — id error must take precedence
        with pytest.raises(ValueError, match="id mismatch"):
            validate_event(make_event(id=BAD_ID, sig=BAD_SIG))
