from bs4 import BeautifulSoup

# Test 1: Uppercase L in Location
html1 = '''
<div class="companyLocation">Bangalore, India</div>
'''
soup1 = BeautifulSoup(html1, "lxml")
matches1 = soup1.select('[class*=location]')
print(f"Uppercase L: {len(matches1)} matches")

# Test 2: Lowercase l in location
html2 = '''
<div class="companylocation">Bangalore, India</div>
'''
soup2 = BeautifulSoup(html2, "lxml")
matches2 = soup2.select('[class*=location]')
print(f"Lowercase l: {len(matches2)} matches")

# Test 3: Mixed case
html3 = '''
<div class="companyLocAtion">Bangalore, India</div>
'''
soup3 = BeautifulSoup(html3, "lxml")
matches3 = soup3.select('[class*=location]')
print(f"Mixed case: {len(matches3)} matches")

# Test 4: Contains "location" as separate word
html4 = '''
<div class="company location">Bangalore, India</div>
'''
soup4 = BeautifulSoup(html4, "lxml")
matches4 = soup4.select('[class*=location]')
print(f"Separate word: {len(matches4)} matches")
if matches4:
    print(f"  Text: {matches4[0].get_text(strip=True)}")

# Test 5: What about data-testid?
html5 = '''
<div data-testid="job-location">Bangalore, India</div>
'''
soup5 = BeautifulSoup(html5, "lxml")
matches5 = soup5.select('[data-testid*=location]')
print(f"data-testid: {len(matches5)} matches")
if matches5:
    print(f"  Text: {matches5[0].get_text(strip=True)}")