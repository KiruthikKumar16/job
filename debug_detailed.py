import sys
import os
# Add the current directory to the path so we can import app and scraper
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

import pandas as pd
from app import _prepare_frame, _parse_skills
from parser import _data_quality_score as _quality_score

# Create a minimal DataFrame with the necessary columns
df = pd.DataFrame([{
    "title": "Test Job",
    "company": "Test Company",
    "location": "Test Location",
    "job_url": "http://test.com/job",
    "description": "Test description",
    "date_posted": None,
    "salary_min": None,
    "salary_max": None,
    "currency": "USD",
    # Note: data_quality_score column is intentionally omitted
    # These columns will be processed by _prepare_frame
    "qualification": "Degree Required",
    "extracted_skills": ["Python", "SQL"],
    "site": "indeed",
    "search_term": "test",
    "seniority": "Mid-Level",
    "work_mode": "Remote"
}])

print("=== INPUT DATAFRAME ===")
print(df.dtypes)
print(df)
print()

# Let's manually step through _prepare_frame to see what happens
result = df.copy()

print("=== AFTER COPY ===")
print(result.dtypes)
print(result)
print()

# Lines 143-146
for column in ("site", "search_term", "title", "company", "location", "qualification", "seniority", "job_url", "work_mode"):
    if column not in result.columns:
        result[column] = ""
    result[column] = result[column].fillna("").astype(str)

print("=== AFTER STRING COLUMNS PROCESSING ===")
print(result.dtypes)
print(result)
print()

# Lines 147-149
if "description" not in result.columns:
    result["description"] = ""
result["description"] = result["description"].fillna("").astype(str)

print("=== AFTER DESCRIPTION PROCESSING ===")
print(result.dtypes)
print(result)
print()

# Lines 150-152
if "extracted_skills" not in result.columns:
    result["extracted_skills"] = [[] for _ in range(len(result))]
result["extracted_skills"] = result["extracted_skills"].map(_parse_skills)

print("=== AFTER EXTRACTED_SKILLS PROCESSING ===")
print(result.dtypes)
print(result)
print()

# Lines 153-156
for column in ("min_exp", "max_exp"):
    if column not in result.columns:
        result[column] = pd.NA
    result[column] = pd.to_numeric(result[column], errors="coerce")

print("=== AFTER NUMERIC COLUMNS PROCESSING ===")
print(result.dtypes)
print(result)
print()

# Lines 165-167
if "date_posted" not in result.columns:
    result["date_posted"] = pd.NaT
result["date_posted"] = pd.to_datetime(result["date_posted"], errors="coerce", utc=True)

print("=== AFTER DATE_POSTED PROCESSING ===")
print(result.dtypes)
print(result)
print()

# Lines 168
result["qualification"] = result["qualification"].replace({"": "Degree Required"})

print("=== AFTER QUALIFICATION REPLACE ===")
print(result.dtypes)
print(result)
print()

# NOW calculate fallback quality
print("=== CALCULATING FALLBACK QUALITY ===")
fallback_quality = result.apply(_quality_score, axis=1)
print(f"fallback_quality: {fallback_quality.iloc[0]}")
print()

# Let's also check what _quality_score sees
row = result.iloc[0]
print("=== ROW FOR QUALITY SCORE ===")
print(row)
print()

print("Field evaluations:")
core_fields = ("title", "company", "location", "job_url", "description", "date_posted")
enriched_fields = ("qualification", "extracted_skills")

present_core = sum(bool(row.get(field)) for field in core_fields)
present_enriched = sum(bool(row.get(field)) for field in enriched_fields)

print(f"Present core fields: {present_core}/{len(core_fields)}")
print(f"Present enriched fields: {present_enriched}/{len(enriched_fields)}")

if present_enriched > 0:
    total_fields = len(core_fields) + len(enriched_fields)
    present = present_core + present_enriched
else:
    total_fields = len(core_fields)
    present = present_core

print(f"Total fields: {total_fields}")
print(f"Present: {present}")
print(f"Score: {round((present / total_fields) * 100) if total_fields > 0 else 0}")