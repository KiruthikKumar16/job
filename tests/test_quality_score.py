"""Tests for parser._data_quality_score scoring logic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import pandas as pd
from jobmarket.job_parser import _data_quality_score

# ---------------------------------------------------------------------------
# Raw (pre-enrichment) data scoring
# ---------------------------------------------------------------------------


class TestRawDataScoring:
    """Scoring rows that have not been through parser.enrich_jobs."""

    def test_all_core_fields_present(self):
        """All six core fields present → 100 %."""
        row = pd.Series(
            {
                "title": "Software Engineer",
                "company": "Tech Corp",
                "location": "New York",
                "job_url": "http://test.com/job1",
                "description": "Python Django experience required",
                "date_posted": "2026-01-01",
                "qualification": None,
                "extracted_skills": None,
            }
        )
        assert _data_quality_score(row) == 100

    def test_half_core_fields_missing(self):
        """Three of six core fields present → 50 %."""
        row = pd.Series(
            {
                "title": "Software Engineer",
                "location": "New York",
                "job_url": "http://test.com/job1",
            }
        )
        assert _data_quality_score(row) == 50


# ---------------------------------------------------------------------------
# Enriched (post-enrichment) data scoring
# ---------------------------------------------------------------------------


class TestEnrichedDataScoring:
    """Scoring rows that include enrichment fields (qualification, extracted_skills)."""

    def test_all_fields_present(self):
        """All core + enriched fields populated → 100 %."""
        row = pd.Series(
            {
                "title": "Software Engineer",
                "company": "Tech Corp",
                "location": "New York",
                "job_url": "http://test.com/job1",
                "description": "Python Django experience required",
                "date_posted": "2026-01-01",
                "qualification": "Bachelor's (Computer Science)",
                "extracted_skills": ["Python", "Django", "APIs"],
            }
        )
        assert _data_quality_score(row) == 100

    def test_enrichment_fields_empty(self):
        """Both enrichment fields are empty (falsy) → fall back to core-only → 100 %."""
        row = pd.Series(
            {
                "title": "Software Engineer",
                "company": "Tech Corp",
                "location": "New York",
                "job_url": "http://test.com/job1",
                "description": "Python Django experience required",
                "date_posted": "2026-01-01",
                "qualification": "",
                "extracted_skills": [],
            }
        )
        assert _data_quality_score(row) == 100

    def test_partial_enrichment(self):
        """One enrichment field present, one empty → 7/8 = 87.5 → rounds to 88."""
        row = pd.Series(
            {
                "title": "Software Engineer",
                "company": "Tech Corp",
                "location": "New York",
                "job_url": "http://test.com/job1",
                "description": "Python Django experience required",
                "date_posted": "2026-01-01",
                "qualification": "Bachelor's (Computer Science)",
                "extracted_skills": [],
            }
        )
        assert _data_quality_score(row) == 88


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary conditions for the scoring function."""

    def test_empty_series(self):
        """Completely empty row → 0."""
        assert _data_quality_score(pd.Series({})) == 0

    def test_all_none(self):
        """All fields explicitly None → 0."""
        row = pd.Series(
            {
                "title": None,
                "company": None,
                "location": None,
                "job_url": None,
                "description": None,
                "date_posted": None,
                "qualification": None,
                "extracted_skills": None,
            }
        )
        assert _data_quality_score(row) == 0
