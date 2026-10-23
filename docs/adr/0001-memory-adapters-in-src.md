# ADR 0001: In-Memory Adapters Placed in src/

**Status:** Accepted

## Context

Testing the application requires in-memory implementations of adapter
interfaces (configuration loaders, the printer transport, the camera) that
avoid real I/O: `RecordingPrinter` records what is sent and answers
queries from a table, `FakeCamera` stores a given image as the photo. The
question is whether these belong under `tests/` or under the production
source tree in `src/datamax_dpl/adapters/memory/`.

## Decision

Place in-memory adapter implementations in
`src/datamax_dpl/adapters/memory/` rather than in the test
tree.

## Consequences

- **Library consumers** can import the same in-memory adapters for their
  own testing without depending on this project's test suite.
- **src/ includes test-support code**, slightly increasing the installed
  package size. This is acceptable because the modules are small and
  carry no heavy dependencies beyond what production code already uses.
- **Tests import from the same package** as production code, keeping
  import paths consistent and avoiding `sys.path` manipulation.
- In-memory adapters follow the same layer rules as production adapters
  (they may depend on domain and application layers but never on
  composition or CLI layers).
