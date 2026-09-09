# Plan: CSV export for the sample-widget service

**Source spec:** `docs/superhuman/specs/2026-01-05-sample-widget-export.md`

## Chunk 1: `export.csv` endpoint

**Files:**
- Create: `app/routes/export.py`
- Create: `tests/test_export.py`

**Acceptance criteria:**
- `GET /widgets/export.csv` returns a CSV response with a header row.
- The endpoint reuses the existing pagination cursor rather than loading the
  whole table.

**Steps:**
- [ ] Step 1: Red -- write a failing test for the endpoint's shape.
- [ ] Step 2: Implement the endpoint.
- [ ] Step 3: Green + docs.
