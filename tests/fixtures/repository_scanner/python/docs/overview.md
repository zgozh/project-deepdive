# Sample service overview

This fixture repository exists so the Phase 2 repository scanner can be exercised
end to end against realistic Python paths.

## Layout

| Path | Purpose |
|---|---|
| `src/sample/service.py` | In-memory order service |
| `tests/test_service.py` | Behaviour tests |
| `scripts/dev.py` | Developer entry point |
| `.github/workflows/ci.yml` | Continuous integration |
| `Dockerfile` | Container image definition |

Nothing here needs a network connection, a package index or a framework.
