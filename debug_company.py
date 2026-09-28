from bs4 import BeautifulSoup

html = '''
<li data-occludable-job-id="456xyz">
    <div class="base-card">
        <h3 class="base-search-card__title">Frontend Engineer</h3>
        <h4 class="base-search-card__subtitle">WebStart Technologies</h4>
        <div class="job-location">Remote • Full-time</div>
        <a class="base-card__full-link" href="https://linkedin.com/jobs/view/456xyz">View Job</a>
    </div>
</li>
'''

soup = BeautifulSoup(html, "lxml")
card = soup.select_one("li[data-occludable-job-id]")
print("Card:", card)

if card:
    # Test company selector
    company_selectors = ["[class*=company]", "[data-testid*=employer]"]
    for selector in company_selectors:
        matches = card.select(selector)
        print(f"Selector '{selector}': {len(matches)} matches")
        if matches:
            for match in matches:
                print(f"  {match.name} class={match.get('class')} data-testid={match.get('data-testid')} -> text={match.get_text(strip=True)}")

    # What we actually have
    print("\nAll elements with class attribute:")
    for elem in card.select('[class]'):
        print(f"  {elem.name} class={elem.get('class')} -> text={elem.get_text(strip=True)}")

    print("\nAll elements with data-testid attribute:")
    for elem in card.select('[data-testid]'):
        print(f"  {elem.name} data-testid={elem.get('data-testid')} -> text={elem.get_text(strip=True)}")