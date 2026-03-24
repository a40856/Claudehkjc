from bs4 import BeautifulSoup
from pathlib import Path
import re
html = Path('data/raw/2026-03-25_HV/inspect.html').read_text()
soup = BeautifulSoup(html,'html.parser')
nav = soup.select_one('div.racingNum.top_races.js_racecard_rt_num')
print('nav', nav is not None)
race_nos=set()
for img in nav.select('img'):
    src = img.get('src','')
    m = re.search(r'racecard_rt_(\d+)(?:_o)?\.gif', src)
    if m:
        race_nos.add(int(m.group(1)))
for a in nav.select('a[href*=RaceNo]'):
    href = a.get('href','')
    m = re.search(r'RaceNo=(\d+)', href)
    if m:
        race_nos.add(int(m.group(1)))
print('numbers', sorted(race_nos))
