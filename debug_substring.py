class_string = "companyLocation"
print(f"Class string: {repr(class_string)}")
print(f"'location' in class string: {'location' in class_string}")
print(f"class_string.find('location'): {class_string.find('location')}")
print(f"len(class_string): {len(class_string)}")
print(f"class_string[7:7+8]: {repr(class_string[7:7+8])}")  # 'location' is 8 chars

# Test the CSS selector
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

# Let's test the selector step by step
print("\nTesting CSS selector '*' (all elements):")
all_elements = soup.select('*')
print(f"Total elements: {len(all_elements)}")

print("\nTesting elements with class attribute:")
elements_with_class = soup.select('[class]')
print(f"Elements with class attribute: {len(elements_with_class)}")
for elem in elements_with_class:
    print(f"  {elem.name}: class={elem.get('class')}")

print("\nTesting [class*=location] selector:")
location_elements = soup.select('[class*=location]')
print(f"Elements matching [class*=location]: {len(location_elements)}")
for elem in location_elements:
    print(f"  {elem.name}: class={elem.get('class')} -> text={elem.get_text(strip=True)}")

# Let's try to understand what *= means in CSS
# According to CSS specs, [attr*=value] matches elements where the attribute value contains the substring value
# So [class*=location] should match elements whose class attribute contains "location" as a substring

# Let's manually check each element with a class
print("\nManual check of each element with class:")
for elem in soup.select('[class]'):
    class_value = ' '.join(elem.get('class')) if elem.get('class') else ""
    contains_location = 'location' in class_value
    print(f"  {elem.name} class={elem.get('class')} -> '{class_value}' contains 'location': {contains_location}")