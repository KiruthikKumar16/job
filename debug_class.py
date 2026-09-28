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
div = soup.find("div", class_="companyLocation")
print("Found div with class_='companyLocation':", div)
if div:
    print("Class attribute:", div.get('class'))
    print("Class as string:", ' '.join(div.get('class')))

# Check if "location" is in the class string
class_string = ' '.join(div.get('class')) if div.get('class') else ""
print("'location' in class string:", "location" in class_string)
print("Class string:", repr(class_string))

# Test the selector manually
print("\nTesting [class*=location] selector:")
matches = soup.select('[class*=location]')
print(f"Found {len(matches)} matches")

# Let's try a different approach - find all elements and check their class
print("\nChecking all elements:")
for elem in soup.find_all(True):  # True means find all tags
    classes = elem.get('class')
    if classes:
        class_str = ' '.join(classes)
        if 'location' in class_str:
            print(f"Element {elem.name} with class '{class_str}' contains 'location'")
            print(f"  Element: {elem}")

# Test if the issue is with the * selector
print("\nTesting other attribute selectors:")
matches2 = soup.select('[class*="company"]')
print(f'[class*="company"]: {len(matches2)} matches')
if matches2:
    print(f"  First: {matches2[0]}")

matches3 = soup.select('div[class*="location"]')
print(f'div[class*="location"]: {len(matches3)} matches')
if matches3:
    print(f"  First: {matches3[0]}")