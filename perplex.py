"""
HKJC Race Card Scraper — Module 1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Collects all pre-race data and writes race_card.json
Run 1-2 days before race day.

Usage:
  python race_card_scraper.py --date 2026/04/26 --venue ST
  python race_card_scraper.py --date 2026/04/22 --venue HV --races 1,2,3
  python race_card_scraper.py --date 2026/04/22 --venue HV   (all races)

Outputs:
  race_card.json         — full structured data for all races
  cache/<horse_id>.json  — horse history cache (reused across days)
"""
from __future__ import annotations
import argparse, json, re, time
from datetime import datetime
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    import sys
    print("[ERROR] Run: pip install requests beautifulsoup4")
    sys.exit(1)

# ─── CONFIG ──────────────────────────────────────────────────────
BASE        = "https://racing.hkjc.com"
CACHE_DIR   = Path("cache")
OUTPUT_FILE = Path("race_card.json")
DELAY       = 1.2   # seconds between requests
CACHE_DAYS  = 3     # days before re-fetching horse history

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://racing.hkjc.com/",
}

# ─── HTTP ─────────────────────────────────────────────────────────
def get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.text
            print(f"  [HTTP {r.status_code}] {url}")
        except Exception as e:
            print(f"  [ERROR] {e} (attempt {attempt+1}/{retries})")
        time.sleep(2)
    return None

def soup(html):
    return BeautifulSoup(html, 'html.parser') if html else None

# ─── HELPERS ──────────────────────────────────────────────────────
def _int(v):
    try:
        return int(str(v).strip().replace('+','').replace(',',''))
    except:
        return None

def _float(v):
    try:
        return float(str(v).strip())
    except:
        return None

# ─── RACE CARD ────────────────────────────────────────────────────
def fetch_race_meta_and_horses(date_str, venue, race_no):
    """
    date_str: YYYY/MM/DD  |  venue: HV or ST  |  race_no: int
    Returns (meta_dict, [horse_dict, ...])
    """
    html = get(f"{BASE}/en-us/local/information/racecard",
               params={"racedate": date_str, "Racecourse": venue, "RaceNo": str(race_no)})
    s = soup(html)
    if not s:
        return {}, []
    meta   = _parse_race_meta(s, race_no)
    horses = _parse_horse_table(s)
    return meta, horses


def _parse_race_meta(s, race_no):
    meta = {"race_no": race_no}
    div  = s.find('div', class_='f_fs13')
    if not div:
        return meta
    for part in div.get_text(separator='|', strip=True).split('|'):
        part = part.strip()
        m = re.match(r'Race \d+\s*-\s*(.+)', part)
        if m:
            meta['race_name'] = m.group(1).strip()
        if re.search(r'\d{4},', part) and ('Happy Valley' in part or 'Sha Tin' in part):
            tm = re.search(r'(\d{1,2}:\d{2})', part)
            vn = re.search(r'(Happy Valley|Sha Tin)', part)
            if tm: meta['start_time']  = tm.group(1)
            if vn: meta['venue_name']  = vn.group(1)
        if 'Turf' in part or 'AWT' in part:
            dm = re.search(r'(\d{3,4})M', part)
            sm = re.search(r'^(Turf|AWT)', part)
            cm = re.search(r'"([^"]+)"\s*Course', part)
            gm = re.search(r',\s*([\w\s]+)$', part)
            if dm: meta['distance']      = int(dm.group(1))
            if sm: meta['surface']       = sm.group(1)
            if cm: meta['course_config'] = cm.group(1)
            if gm: meta['going']         = gm.group(1).strip()
        if 'Rating:' in part:
            rm = re.search(r'Rating:\s*(\d+)[-\u2013](\d+)', part)
            cl = re.search(r'Class\s+(\d)', part)
            if rm: meta['rating_range'] = f"{rm.group(1)}-{rm.group(2)}"
            if cl: meta['race_class']   = int(cl.group(1))
    return meta


def _parse_horse_table(s):
    for t in s.find_all('table'):
        hr = t.find('tr')
        if not hr:
            continue
        ths = [c.get_text(strip=True) for c in hr.find_all(['th','td'])]
        if 'Draw' not in ths or 'Jockey' not in ths:
            continue
        horses = []
        for row in t.find_all('tr')[1:]:
            cells  = [td.get_text(strip=True) for td in row.find_all('td')]
            if not cells or not cells[0]:
                continue
            is_scr = any('TdScratch' in str(td.get('class','')) for td in row.find_all('td'))
            links  = {}
            for a in row.find_all('a'):
                href = a.get('href','')
                if 'horseid='   in href:
                    m = re.search(r'horseid=(\S+)', href);   links['horse_id']   = m.group(1) if m else ''
                elif 'jockeyid=' in href:
                    m = re.search(r'jockeyid=(\w+)', href);  links['jockey_id']  = m.group(1) if m else ''
                elif 'trainerid=' in href:
                    m = re.search(r'trainerid=(\w+)', href); links['trainer_id'] = m.group(1) if m else ''
            h = dict(zip(ths, cells))
            h.update(links)
            h['scratched']      = is_scr
            h['no']             = h.pop('Horse No.', '').strip()
            h['name']           = h.pop('Horse', '').strip()
            h['draw']           = _int(h.pop('Draw', ''))
            h['carried_wt']     = _int(h.pop('Wt.', ''))
            h['over_wt']        = _int(h.pop('Over Wt.', ''))
            h['jockey']         = h.pop('Jockey', '').strip()
            h['trainer']        = h.pop('Trainer', '').strip()
            h['rating']         = _int(h.pop('Rtg.', ''))
            h['rating_chg']     = _int(h.pop('Rtg.+/-', ''))
            h['horse_wt']       = _int(h.pop('Horse Wt. (Declaration)', ''))
            h['horse_wt_chg']   = _int(h.pop('Wt.+/- (vs Declaration)', ''))
            h['best_time']      = h.pop('Best Time', '').strip()
            h['age']            = _int(h.pop('Age', ''))
            h['sex']            = h.pop('Sex', '').strip()
            h['gear']           = h.pop('Gear', '').strip()
            h['days_since_run'] = _int(h.pop('Days since Last Run', ''))
            h['last_6_runs']    = h.pop('Last 6 Runs', '').strip()
            h['sire']           = h.pop('Sire', '').strip()
            h['dam']            = h.pop('Dam', '').strip()
            h['import_cat']     = h.pop('Import Cat.', '').strip()
            h['season_stakes']  = h.pop('Season Stakes', '').replace(',','').strip()
            for k in ["Brand No.", "Int'l Rtg.", 'WFA', 'Colour', 'Priority', 'Owner', '']:
                h.pop(k, None)
            horses.append(h)
        return horses
    return []


# ─── HORSE HISTORY ────────────────────────────────────────────────
def fetch_horse_history(horse_id):
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{horse_id}.json"
    if cache_file.exists():
        age_days = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 86400
        if age_days < CACHE_DAYS:
            return json.loads(cache_file.read_text())

    html = get(f"{BASE}/en-us/local/information/horse", params={"horseid": horse_id})
    s    = soup(html)
    if not s:
        return {}
    time.sleep(DELAY)

    # Meta
    meta = {}
    for t in s.find_all('table'):
        if 'Current Rating' in t.get_text() and 'Trainer' in t.get_text():
            for row in t.find_all('tr'):
                cells = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cells) >= 3 and cells[1] == ':' and cells[0] != 'Same Sire':
                    meta[cells[0]] = cells[2]
            break

    # Form history
    form_rows = []
    for t in s.find_all('table'):
        hr = t.find('tr')
        if not hr:
            continue
        ths = [c.get_text(strip=True) for c in hr.find_all(['th','td'])]
        if 'Jockey' not in ths or 'LBW' not in ths:
            continue
        season = ''
        for row in t.find_all('tr')[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if not cells:
                continue
            if len(cells) == 1 and 'Season' in cells[0]:
                season = cells[0]; continue
            if len(cells) < 10:
                continue
            rd = dict(zip(ths, cells))
            rd['season'] = season
            rc = rd.get('RC/Track/Course', '')
            vm = re.match(r'(HV|ST|CHA)', rc)
            sm = re.search(r'(Turf|AWT)', rc)
            cm = re.search(r'"([^"]+)"', rc)
            rd['_venue']   = vm.group(1) if vm else ''
            rd['_surface'] = sm.group(1) if sm else ''
            rd['_config']  = cm.group(1) if cm else ''
            rd['_place']   = _int(rd.get('Pla.',''))
            rd['_dist']    = _int(rd.get('Dist.',''))
            rd['_draw']    = _int(rd.get('Dr.',''))
            rd['_class']   = _int(rd.get('RaceClass',''))
            rd['_odds']    = _float(rd.get('Win Odds',''))
            form_rows.append(rd)
        break

    profiles = _build_profiles(form_rows)
    result   = {
        "horse_id":   horse_id,
        "fetched_at": datetime.now().isoformat(),
        "meta":       meta,
        "form":       form_rows,
        "profiles":   profiles,
    }
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _build_profiles(form_rows):
    def wr(rows):
        wins = sum(1 for r in rows if r.get('_place') == 1)
        n    = len(rows)
        return {"wins": wins, "starts": n, "win_pct": round(wins/n*100,1) if n else 0}

    if not form_rows:
        return {}

    by_dist    = {str(d): wr([r for r in form_rows if r['_dist']==d])
                  for d in set(r['_dist'] for r in form_rows if r['_dist'])}
    by_venue   = {v: wr([r for r in form_rows if r['_venue']==v])
                  for v in set(r['_venue'] for r in form_rows if r['_venue'])}
    by_surface = {sv: wr([r for r in form_rows if r['_surface']==sv])
                  for sv in set(r['_surface'] for r in form_rows if r['_surface'])}
    by_jockey  = {j: wr([r for r in form_rows if r.get('Jockey','')==j])
                  for j in set(r.get('Jockey','') for r in form_rows if r.get('Jockey',''))
                  if sum(1 for r in form_rows if r.get('Jockey','')==j) >= 2}
    by_trainer = {tn: wr([r for r in form_rows if r.get('Trainer','')==tn])
                  for tn in set(r.get('Trainer','') for r in form_rows if r.get('Trainer',''))
                  if sum(1 for r in form_rows if r.get('Trainer','')==tn) >= 2}

    recent     = form_rows[:6]
    rec_pos    = [r['_place'] for r in recent if r['_place']]
    avg_pos    = round(sum(rec_pos)/len(rec_pos),1) if rec_pos else None
    top3_rate  = round(sum(1 for p in rec_pos if p and p<=3)/len(rec_pos)*100,1) if rec_pos else 0

    last_date_str = form_rows[0].get('Date','') if form_rows else ''
    days_off  = 0
    if last_date_str:
        try:
            days_off = (datetime.now() - datetime.strptime(last_date_str,'%d/%m/%y')).days
        except:
            pass

    return {
        "by_distance":         by_dist,
        "by_venue":            by_venue,
        "by_surface":          by_surface,
        "by_jockey":           by_jockey,
        "by_trainer":          by_trainer,
        "recent_form": {
            "last_6_positions": rec_pos,
            "avg_position":     avg_pos,
            "top3_rate_pct":    top3_rate,
        },
        "days_since_last_run": days_off,
        "long_absence_flag":   days_off > 60,
    }


# ─── JOCKEY / TRAINER STATS ───────────────────────────────────────
def fetch_jockey_stats():
    html = get(f"{BASE}/en-us/local/information/jkcstat")
    s    = soup(html)
    result = {}
    if not s:
        return result
    for t in s.find_all('table'):
        rows = t.find_all('tr')
        if len(rows) < 4:
            continue
        for row in rows[3:]:
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) < 3:
                continue
            link = row.find('a')
            jid  = ''
            if link:
                m = re.search(r'jockeyid=(\w+)', link.get('href',''))
                if m: jid = m.group(1)
            name = cells[1] if len(cells) > 1 else ''
            if not name:
                continue
            pts   = []
            for p in cells[2:12]:
                try: pts.append(int(p))
                except: pts.append(None)
            valid = [p for p in pts if p is not None]
            result[name] = {
                'jockey_id':  jid,
                'jkc_rank':   _int(cells[0]),
                'recent_pts': pts[:10],
                'avg_pts':    round(sum(valid)/len(valid),1) if valid else 0,
            }
    return result


def fetch_trainer_stats():
    html = get(f"{BASE}/en-us/local/information/tncstat")
    s    = soup(html)
    result = {}
    if not s:
        return result
    for t in s.find_all('table'):
        rows = t.find_all('tr')
        if len(rows) < 4:
            continue
        for row in rows[3:]:
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) < 3:
                continue
            link = row.find('a')
            tid  = ''
            if link:
                m = re.search(r'trainerid=(\w+)', link.get('href',''))
                if m: tid = m.group(1)
            name = cells[1] if len(cells) > 1 else ''
            if not name:
                continue
            pts   = []
            for p in cells[2:12]:
                try: pts.append(int(p))
                except: pts.append(None)
            valid = [p for p in pts if p is not None]
            result[name] = {
                'trainer_id': tid,
                'tnc_rank':   _int(cells[0]),
                'recent_pts': pts[:10],
                'avg_pts':    round(sum(valid)/len(valid),1) if valid else 0,
            }
    return result


# ─── DRAW STATS ───────────────────────────────────────────────────
def fetch_draw_stats(date_str, venue):
    html = get(f"{BASE}/en-us/local/information/draw",
               params={"date": date_str.replace('/','-'), "venue": venue})
    s    = soup(html)
    draw_by_race = {}
    if not s:
        return draw_by_race
    for t in s.find_all('table'):
        hr = t.find('tr')
        if not hr:
            continue
        htxt = hr.get_text(strip=True).replace('\xa0','').strip()
        if not (re.search(r'Race \d+', htxt) and re.search(r'\d{3,4}m', htxt, re.I)):
            continue
        rn_m = re.search(r'Race (\d+)', htxt)
        if not rn_m:
            continue
        rn    = int(rn_m.group(1))
        gates = {}
        for row in t.find_all('tr')[2:]:
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if cells and cells[0].isdigit():
                g = int(cells[0])
                gates[g] = {
                    'runners':       _int(cells[1]) if len(cells)>1 else 0,
                    'wins':          _int(cells[2]) if len(cells)>2 else 0,
                    'win_pct':       _int(cells[6]) if len(cells)>6 else 0,
                    'quinella_pct':  _int(cells[7]) if len(cells)>7 else 0,
                    'place_pct':     _int(cells[8]) if len(cells)>8 else 0,
                }
        draw_by_race[rn] = gates
    return draw_by_race


# ─── MAIN ASSEMBLER ───────────────────────────────────────────────
def build_race_card(date_str, venue, race_numbers=None):
    print(f"\n{'='*60}")
    print(f"  HKJC Race Card Scraper  |  {date_str}  {venue}")
    print(f"{'='*60}")

    print("\n[1/4] Jockey stats...", end=' ', flush=True)
    jky_stats = fetch_jockey_stats()
    print(f"{len(jky_stats)} jockeys ✓")
    time.sleep(DELAY)

    print("[2/4] Trainer stats...", end=' ', flush=True)
    tnr_stats = fetch_trainer_stats()
    print(f"{len(tnr_stats)} trainers ✓")
    time.sleep(DELAY)

    print("[3/4] Draw stats...", end=' ', flush=True)
    draw_stats = fetch_draw_stats(date_str, venue)
    print(f"{len(draw_stats)} races ✓")
    time.sleep(DELAY)

    print("[4/4] Race cards + horse histories...")
    races_output = []

    for rn in (race_numbers or range(1, 12)):
        print(f"\n  ── Race {rn} ──")
        meta, horses = fetch_race_meta_and_horses(date_str, venue, rn)
        if not horses:
            print(f"  No data — end at Race {rn-1}")
            break
        time.sleep(DELAY)

        for h in horses:
            hid = h.get('horse_id','')
            tag = "SCR" if h['scratched'] else hid
            print(f"    #{h['no']:>2} {h['name']:<22} [{tag}]", end=' ', flush=True)

            if hid and not h['scratched']:
                hist     = fetch_horse_history(hid)
                profiles = hist.get('profiles', {})
                h['history']          = hist
                h['profile_distance'] = profiles.get('by_distance',{}).get(str(meta.get('distance')),{})
                h['profile_venue']    = profiles.get('by_venue',{}).get(venue,{})
                h['profile_surface']  = profiles.get('by_surface',{}).get(meta.get('surface',''),{})
                h['profile_jockey']   = profiles.get('by_jockey',{}).get(h.get('jockey',''),{})
                h['profile_trainer']  = profiles.get('by_trainer',{}).get(h.get('trainer',''),{})
                h['recent_form']      = profiles.get('recent_form',{})
                h['long_absence']     = profiles.get('long_absence_flag', False)
                n_races = len(hist.get('form',[]))
                print(f"✓ {n_races} career races")
            else:
                h['history'] = {}
                print("skipped")
            time.sleep(DELAY)

            # Jockey stats (strip apprentice weight allowance)
            jky_clean = re.sub(r'\s*\(-\d+\)', '', h.get('jockey','')).strip()
            h['jockey_stats']  = jky_stats.get(jky_clean, jky_stats.get(h.get('jockey',''), {}))
            h['trainer_stats'] = tnr_stats.get(h.get('trainer',''), {})

            # Draw stats
            gate = h.get('draw')
            h['draw_stats'] = draw_stats.get(rn, {}).get(gate, {}) if gate else {}

        races_output.append({
            "race_no":   rn,
            "race_meta": meta,
            "horses":    horses,
            "draw_bias": draw_stats.get(rn, {}),
        })
        print(f"  Race {rn} ✓  ({len(horses)} horses)")

    output = {
        "scraped_at":    datetime.now().isoformat(),
        "date":          date_str,
        "venue":         venue,
        "total_races":   len(races_output),
        "races":         races_output,
        "jockey_stats":  jky_stats,
        "trainer_stats": tnr_stats,
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str))

    total_horses = sum(len(r['horses']) for r in races_output)
    print(f"\n{'='*60}")
    print(f"  ✅ Saved: {OUTPUT_FILE}")
    print(f"  Races: {len(races_output)}  |  Horses: {total_horses}")
    print(f"{'='*60}\n")
    return output


# ─── CLI ──────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="HKJC Race Card Scraper — Module 1")
    ap.add_argument("--date",   required=True,           help="Race date YYYY/MM/DD")
    ap.add_argument("--venue",  required=True,           help="Venue: HV or ST", choices=["HV","ST"])
    ap.add_argument("--races",  default=None,            help="e.g. 1,2,3  (default: all)")
    ap.add_argument("--output", default="race_card.json",help="Output JSON path")
    args = ap.parse_args()

    global OUTPUT_FILE
    OUTPUT_FILE = Path(args.output)

    race_numbers = [int(x.strip()) for x in args.races.split(',')] if args.races else None
    build_race_card(args.date, args.venue, race_numbers)

if __name__ == "__main__":
    main()
