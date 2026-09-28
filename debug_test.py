import sys
import os
# Add the current directory to the path so we can import app and scraper
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

import pandas as pd
from app import _prepare_frame
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

print("Input DataFrame:")
print(df)
print()

# Process the frame
result = _prepare_frame(df)

print("Result DataFrame:")
print(result)
print()

# Calculate what the fallback score should be for this row
expected_quality = _quality_score(result.iloc[0])
print(f"Expected quality score: {expected_quality}")
print(f"Actual quality score in result: {result.iloc[0]['data_quality_score']}")
print(f"Equal? {result.iloc[0]['data_quality_score'] == expected_quality}")

# Let's also check what the individual fields evaluate to
row = result.iloc[0]
print("\nField evaluations:")
core_fields = ("title", "company", "location", "job_url", "description", "date_posted")
enriched_fields = ("qualification", "extracted_skills")

for field in core_fields:
    value = row.get(field)
    print(f"  {field}: {repr(value)} -> bool: {bool(value)}")

for field in enriched_fields:
    value = row.get(field)
    print(f"  {field}: {repr(value)} -> bool: {bool(value)}")