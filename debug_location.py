from scraper import _extract_browser_cards

html = '''
<div class="job_seen_beacon">
    <h2 class="jobTitle">Senior Python Developer</h2>
    <div class="companyName">Tech Solutions Inc</div>
    <div class="companyLocation">Bangalore, India</div>
    <a href="/viewjob?jk=123abc" class="jobtitle">Apply Now</a>
</div>
'''

result = _extract_browser_cards(html, "indeed", "Unknown Location", limit=1)
print("Result:")
print(result)
if not result.empty:
    print("\nLocation value:", repr(result.iloc[0]["location"]))
    print("Expected: 'Bangalore, India'")
else:
    print("No results found")