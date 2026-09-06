"""Corpus-shaped fixture suite (TEST.md section 7 / FR-2's "real fixtures" clause).

FR-2 requires the reader be "exercised against real corpus fixtures, not
synthetic ones only." Per NFR-4 (this is a public repo) and the
`.publication-tokens` deny-list (which flags the operator's org and product
vocabulary as a substring, case-insensitively, across every tracked file),
the actual corpus text cannot be committed verbatim -- every private repo
name, slug, and decision line in the real corpus trips that guard. Chunk 1
already established the pattern this suite follows: each fixture under
`tests/fixtures/gate_records/` reproduces one real corpus record's *shape*
(version-gate boundary, bullet/bold/date-only/sub-gate/non-gate line forms,
multi-line HTML comments, LD-style locked blocks, front-matter-only gate
mentions) with invented, generic content standing in for anything
identifying.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_record_parser as grp  # noqa: E402
import superhuman_profile as sp  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gate_records"


def test_bullet_prefixed_and_locked_v1_1_0_fixture_passes_through_g4() -> None:
    """The bullet/HTML-comment corpus shape (v1.1.0, populated locked block) is well-formed."""
    reading = grp.read_record(FIXTURES / "bullet_prefixed_and_html_comment.md")
    assert reading.version_gate is True
    assert reading.locked.well_formed is True
    assert sp.gate_record_gap(reading, gate=4) is None


def test_bold_wrapped_g3_label_registers_in_the_no_locked_block_fixture() -> None:
    """The `**G3: ABORT...**` shape (v1.0.1, no locked block) registers G3, not G0-only."""
    reading = grp.read_record(FIXTURES / "no_locked_block_bold_gate_label.md")
    assert reading.version_gate is False
    assert 3 in reading.log.gates
    assert reading.log.highest_gate == 3


def test_duplicate_slug_fixture_pair_is_byte_identical() -> None:
    """The NFR-7 duplicate-slug pairing fixtures are byte-identical, as the real pair is."""
    original = (FIXTURES / "no_locked_block_bold_gate_label.md").read_bytes()
    duplicate = (FIXTURES / "no_locked_block_bold_gate_label_duplicate.md").read_bytes()
    assert original == duplicate


def test_date_only_and_prose_gate_mention_fixture_excludes_the_prose_gate() -> None:
    """The G9-in-front-matter-prose shape never registers; presence reads the true section."""
    reading = grp.read_record(FIXTURES / "date_only_and_prose_gate_mention.md")
    assert reading.version_gate is True
    assert 9 not in reading.locked.gates
    assert reading.locked.highest_gate == 1


def test_ld_style_locked_block_fixture_fails_well_formedness() -> None:
    """The bold-ID/no-timestamp locked-block shape does not match the entry grammar.

    This is the corpus's one non-conforming record (OI-3): its own repair is
    explicitly out of this chunk's scope (PLAN chunk 4), so the expected,
    documented behavior here is that the record fails well-formedness
    rather than crashing the parser.
    """
    reading = grp.read_record(FIXTURES / "ld_style_locked_block.md")
    assert reading.locked.found is True
    assert reading.locked.malformed != ()
    gap = sp.gate_record_gap(reading, gate=1)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD
    # G10 still escapes even this non-conforming record (FR-14).
    assert sp.gate_record_gap(reading, gate=10) is None


def test_self_dogfood_subgate_fixture_registers_g6_from_g6_001() -> None:
    """The `G6-001` compound-label shape contributes gate 6, exactly as a bare `G6:` would."""
    reading = grp.read_record(FIXTURES / "self_dogfood_subgate.md")
    assert 6 in reading.locked.gates


@pytest.mark.parametrize("variant", ["crlf_variant.md", "cr_variant.md"])
def test_line_ending_variants_read_identically_to_the_lf_original(variant: str) -> None:
    """CRLF/CR variants of the same record read to the same gate set and well-formedness (NFR-3)."""
    baseline = grp.read_record(FIXTURES / "date_only_and_prose_gate_mention.md")
    variant_reading = grp.read_record(FIXTURES / variant)
    assert variant_reading.version == baseline.version
    assert variant_reading.locked.gates == baseline.locked.gates
    assert variant_reading.locked.well_formed == baseline.locked.well_formed
    assert variant_reading.log.gates == baseline.log.gates


def test_no_locked_block_at_1_1_0_fixture_fails_closed() -> None:
    """The synthetic fresh-project shape (v1.1.0, no locked block yet) fails closed (R6)."""
    reading = grp.read_record(FIXTURES / "no_locked_block_v1_1_0.md")
    assert reading.version_gate is True
    assert reading.locked.found is False
    gap = sp.gate_record_gap(reading, gate=5)
    assert gap is not None
    assert gap.code == sp.EXIT_RECORD


@pytest.mark.parametrize(
    "variant",
    [
        "malformed_version_1_1.md",
        "malformed_version_v1_1_0.md",
        "malformed_version_1_1_0_beta.md",
        "malformed_version_latest.md",
        "malformed_version_empty.md",
        "malformed_version_banana.md",
    ],
)
def test_malformed_version_fixtures_fall_into_the_tolerant_bucket(variant: str) -> None:
    """Every malformed-version corpus shape resolves to the tolerant (< 1.1.0) bucket (G4-R2)."""
    reading = grp.read_record(FIXTURES / variant)
    assert reading.version_gate is False
    assert sp.gate_record_gap(reading, gate=5) is None


def test_every_mandatory_gate_present_fixture_passes_g8() -> None:
    """A record carrying every mandatory gate passes the highest one, G8."""
    reading = grp.read_record(FIXTURES / "every_mandatory_gate_present.md")
    assert sp.gate_record_gap(reading, gate=8) is None


def test_g5_no_g6_fixture_passes_g7() -> None:
    """FR-13's headline case, from a fixture file: G5 on record, no G6, G7 still passes."""
    reading = grp.read_record(FIXTURES / "g5_no_g6_requesting_g7.md")
    assert sp.gate_record_gap(reading, gate=7) is None


def test_unknown_sentinel_fixture_satisfies_presence_through_g6() -> None:
    """UNKNOWN-stamped gates in a fixture file still satisfy presence for the next gate."""
    reading = grp.read_record(FIXTURES / "unknown_sentinel_variants.md")
    assert sp.gate_record_gap(reading, gate=6) is None


def test_maximally_broken_fixture_only_escapes_at_g10() -> None:
    """The maximally-broken fixture fails every gate except G10."""
    reading = grp.read_record(FIXTURES / "maximally_broken.md")
    assert sp.gate_record_gap(reading, gate=10) is None
    assert sp.gate_record_gap(reading, gate=1) is not None
