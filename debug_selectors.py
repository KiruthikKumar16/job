from bs4 import BeautifulSoup

html = '''
<div class="job_seen_beacon">
    <h2 class="jobTitle">Senior Python Developer</h2>
    <div class="companyName">Tech Solutions Inc</div>
    <div class="companyLocation">Bangalore, India</div>
    <a href="/viewjob?jk=123abc" class="jobtitle">Apply Now</a>
</div>
'''

soup = BeautifulSoup(html, "lxml")
print("Full HTML:")
print(soup.prettify())

print("\nTesting selectors:")
selectors = [
    "li[data-occludable-job-id]", "div.job_seen_beacon", "article[data-testid*=job]",
    "li[class*=job]", "div[class*=job-card]", "article[class*=job]",
]

for selector in selectors:
    matches = soup.select(selector)
    print(f"Selector '{selector}': {len(matches)} matches")
    if matches:
        print(f"  First match: {matches[0]}")

print("\nTesting location selectors:")
location_selectors = ["[class*=location]", "[data-testid*=location]"]
for selector in location_selectors:
    matches = soup.select(selector)
    print(f"Selector '{selector}': {len(matches)} matches")
    if matches:
        for i, match in enumerate(matches):
            print(f"  Match {i}: {match} -> text: {match.get_text(strip=True)}")

# Also test what _text function does
def _text(value):
    if value is None:
        return ""
    try:
        import pandas as pd
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()

print("\nTesting _text on matches:")
for selector in location_selectors:
    matches = soup.select(selector)
    if matches:
        location_node = matches[0]
        text = location_node.get_text(" ", strip=True)
        print(f"Raw text from {selector}: {repr(text)}")
        processed = _text(text)
        print(f"After _text: {repr(processed)}")