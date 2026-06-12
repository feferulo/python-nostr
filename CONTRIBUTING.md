# Contributing to python-nostr

## Documentation conventions

Design decisions for each component are documented in a `docs/` directory
co-located with the package they belong to:

```
src/
└── nostr_relay/
    ├── docs/
    │   ├── STORE.md          # EventStore schema, indexes, query strategy
    │   └── CONNECTION.md     # connection manager, send queue, backpressure
    └── store.py
    └── connection.py
```

When adding a non-trivial component, create a corresponding markdown file in
the relevant `docs/` directory. A good design document covers:

- **What the component does** — its responsibilities and public interface
- **Key design decisions** — why this approach over alternatives
- **Schema or data structures** — if the component owns persistent state
- **Trade-offs** — what was explicitly deferred or ruled out and why

Documents should be written for a reader who understands Python and the Nostr
protocol but is unfamiliar with this specific implementation.

## NIP compliance

Each implemented NIP is tracked in the README compliance table. When
implementing a new NIP, update the table and link to the relevant spec.

## Testing conventions

- Tests live in `tests/` mirroring the `src/` structure:
  - `tests/test_core/` for `nostr_core`
  - `tests/test_relay/` for `nostr_relay`
  - `tests/test_client/` for `nostr_client`
- Tests are written before implementation (TDD)
- One test file per module: `store.py` → `test_store.py`
- Fixtures are used for objects that require construction or teardown;
  plain constants or helper functions for simple values
- Parametrize when test logic is identical and only the input varies;
  individual tests when assertions or setup differ meaningfully

## Running tests

```bash
uv run pytest
```
