# Render entrypoint for the live match selector + production OBS scoreboard.
# This file deliberately reuses the production OBS app and only adds the selector.
from flask import Response, request, jsonify, redirect
from urllib.parse import quote
from html import escape
import re
import time
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
import entry
import main

app = entry.app
HEADERS = dict(main.HEADERS)
HEADERS.update({
    "User-Agent": "Mozilla/5.0 (cricket-live-overlay/1.0)",
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
})
SELECTOR_SOURCES = [
    "https://www.cricbuzz.com/cricket-match/live-scores",
    "https://m.cricbuzz.com/cricket-match/live-scores",
]
UPCOMING_SOURCES = [
    "https://www.cricbuzz.com/cricket-schedule/upcoming-series/all",
    "https://www.cricbuzz.com/cricket-match/live-scores/upcoming-matches",
]



def get_matches():
    matches, seen = [], set()
    for url in SELECTOR_SOURCES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                m = re.search(r"/live-cricket-scores/(\d+)(?:/([^?#\"']+))?", href)
                if not m:
                    continue
                mid, slug = m.group(1), (m.group(2) or "")
                if mid in seen:
                    continue
                text = " ".join(a.stripped_strings) or slug.replace("-", " ").title()
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) < 5 or "scorecard" in text.lower():
                    continue
                seen.add(mid)
                matches.append({"id": mid, "name": text[:180],
                    "url": f"https://www.cricbuzz.com/live-cricket-scores/{mid}/{slug}" if slug else f"https://www.cricbuzz.com/live-cricket-scores/{mid}"})
        except Exception as exc:
            print("selector source error:", repr(exc))
        if len(matches) >= 12:
            break
    if not any(str(x["id"]) == "151543" for x in matches):
        matches.insert(0, {"id":"151543","name":"India vs West Indies — 2nd ODI",
            "url":"https://www.cricbuzz.com/live-cricket-scores/151543/ind-vs-wi-2nd-odi-india-v-west-indies"})
    return matches[:30]


def get_upcoming_matches(exclude_ids=None):
    """Discover future matches from Cricbuzz instead of a hard-coded list."""
    exclude_ids = {str(x) for x in (exclude_ids or [])}
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    found, seen = [], set()
    for url in UPCOMING_SOURCES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                m = re.search(r"/live-cricket-scores/(\d+)(?:/([^?#\"']+))?", href)
                if not m:
                    continue
                mid, slug = m.group(1), (m.group(2) or "")
                if mid in seen or mid in exclude_ids:
                    continue
                name = re.sub(r"\s+", " ", " ".join(a.stripped_strings) or slug.replace("-", " ").title()).strip()
                if len(name) < 5 or "scorecard" in name.lower():
                    continue
                context = ""
                parent = a
                for _ in range(6):
                    parent = getattr(parent, "parent", None)
                    if not parent:
                        break
                    context = " ".join(parent.stripped_strings)
                    if re.search(r"(?i)(?:mon|tue|wed|thu|fri|sat|sun)[,\s]+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,|\s)+\d{4}", context):
                        break
                dm = re.search(r"(?i)(?:mon|tue|wed|thu|fri|sat|sun)[,\s]+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:,|\s)+(\d{4})", context)
                if not dm:
                    continue
                try:
                    match_date = datetime.strptime(f"{dm.group(1)[:3]} {dm.group(2)} {dm.group(3)}", "%b %d %Y").date()
                except Exception:
                    continue
                if match_date < today:
                    continue
                venue = ""
                vm = re.search(r"(?i)•\s*([^•]+?)(?:\s+\d{1,2}:\d{2}|$)", context)
                if vm:
                    venue = re.sub(r"\s+", " ", vm.group(1)).strip()
                found.append({"id":mid,"name":name[:180],"date":match_date.strftime("%d %b %Y"),
                    "time":"","venue":venue[:180],"url":f"https://www.cricbuzz.com/live-cricket-scores/{mid}/{slug}" if slug else f"https://www.cricbuzz.com/live-cricket-scores/{mid}","_date":match_date})
                seen.add(mid)
        except Exception as exc:
            print("upcoming source error:", repr(exc))
        if len(found) >= 40:
            break
    found.sort(key=lambda x:(x["_date"],x["name"]))
    for item in found:
        item.pop("_date",None)
    return found[:40]


def _fallback_live(match):
    """Build the exact scoreboard schema directly from Cricbuzz live JSON.
    This prevents the OBS page from being stuck at LOADING when the richer parser
    cannot resolve a scorecard snapshot or captain metadata.
    """
    mid = str(match["id"])
    url = str(match.get("url") or f"https://www.cricbuzz.com/live-cricket-scores/{mid}")
    match = dict(match)
    match["url"] = url
    live_url = f"https://www.cricbuzz.com/api/mcenter/comm/{mid}?obs_ts={time.time_ns()}"
    try:
        r = requests.get(live_url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print("fallback live fetch error:", repr(exc))
        return None

    mini = payload.get("miniscore") or {}
    header = payload.get("matchHeader") or {}
    t1 = main._team_name(header.get("team1")) if hasattr(main, "_team_name") else ""
    t2 = main._team_name(header.get("team2")) if hasattr(main, "_team2") else ""
    if not t1:
        t1 = (header.get("team1") or {}).get("teamName") or (header.get("team1") or {}).get("name") or "TEAM 1"
    if not t2:
        t2 = (header.get("team2") or {}).get("teamName") or (header.get("team2") or {}).get("name") or "TEAM 2"

    bat = mini.get("batTeam") or {}
    bat_obj = mini.get("batTeamScoreObj") or {}
    bat_name = bat_obj.get("teamName") or bat_obj.get("teamFullName") or bat_obj.get("name") or bat.get("teamName") or bat.get("teamShortName") or mini.get("batTeamName") or ""
    runs = bat.get("teamScore", bat.get("score", mini.get("teamScore")))
    wkts = bat.get("teamWkts", bat.get("wickets", mini.get("teamWkts")))
    overs = mini.get("overs", mini.get("oversStr", ""))
    if runs is None or wkts is None:
        return None

    def norm(v):
        return re.sub(r"[^a-z0-9]", "", str(v or "").lower())
    def team_match(a, b):
        a, b = norm(a), norm(b)
        return bool(a and b and (a == b or a in b or b in a))

    if team_match(bat_name, t1):
        bi = 0
    elif team_match(bat_name, t2):
        bi = 1
    else:
        bi = 0

    def player(obj, striker=False):
        if not isinstance(obj, dict):
            return None
        name = obj.get("batName") or obj.get("name") or obj.get("batsmanName")
        if not name:
            return None
        return {"name": str(name), "runs": str(obj.get("batRuns", obj.get("runs", obj.get("r", 0)))), "balls": str(obj.get("batBalls", obj.get("balls", obj.get("b", 0)))), "striker": bool(striker)}

    def bowler(obj):
        if not isinstance(obj, dict):
            return None
        name = obj.get("bowlName") or obj.get("name") or obj.get("bowlerName")
        if not name:
            return None
        return {"name": str(name), "overs": str(obj.get("bowlOvs", obj.get("overs", obj.get("o", "")))), "maidens": str(obj.get("bowlMaidens", obj.get("maidens", obj.get("m", 0)))), "runs": str(obj.get("bowlRuns", obj.get("runs", obj.get("r", 0)))), "wickets": str(obj.get("bowlWkts", obj.get("wickets", obj.get("w", 0)))), "economy": str(obj.get("bowlEcon", obj.get("economy", obj.get("eco", ""))))}

    bats = [x for x in [player(mini.get("batsmanStriker"), True), player(mini.get("batsmanNonStriker"), False)] if x]
    bo = bowler(mini.get("bowlerStriker") or mini.get("bowler") or mini.get("currentBowler"))
    partnership = mini.get("partnership") or mini.get("partnerShip") or mini.get("partnershipObj")
    if isinstance(partnership, dict):
        pr = partnership.get("runs", partnership.get("partnershipRuns", partnership.get("r")))
        pb = partnership.get("balls", partnership.get("partnershipBalls", partnership.get("b")))
        partnership = str(pr) + (f" ({pb})" if pb is not None else "") if pr is not None else "-"
    elif partnership is None:
        partnership = "-"
    else:
        partnership = str(partnership)

    try:
        crr = float(mini.get("currentRunRate", mini.get("crr")))
        crr = f"{crr:.2f}"
    except Exception:
        crr = str(mini.get("currentRunRate", mini.get("crr", "-")))

    flags = {"india": "🇮🇳", "westindies": "🌴", "australia": "🇦🇺", "england": "🏴", "pakistan": "🇵🇰", "southafrica": "🇿🇦", "srilanka": "🇱🇰", "bangladesh": "🇧🇩", "newzealand": "🇳🇿", "afghanistan": "🇦🇫"}
    def flag(team):
        return flags.get(norm(team), "🏳️")

    captains = []
    try:
        captains = entry.wsgi._captains(match, t1, t2) or []
    except Exception as exc:
        print("fallback captain parse error:", repr(exc))
    if len(captains) < 2:
        captains = (captains + [{"name": "Captain 1"}, {"name": "Captain 2"}])[:2]

    return {
        "title": f"{t1} vs {t2}", "url": url,
        "team1": t1, "team2": t2,
        "team1_code": main.code(t1), "team2_code": main.code(t2),
        "team1_flag": flag(t1), "team2_flag": flag(t2),
        "team1_score": f"{runs}-{wkts}" if bi == 0 else "-",
        "team2_score": f"{runs}-{wkts}" if bi == 1 else "-",
        "team1_overs": str(overs) if bi == 0 else "",
        "team2_overs": str(overs) if bi == 1 else "",
        "batting_index": bi, "bowling_index": 1 - bi,
        "batsmen": bats, "bowler": bo, "partnership": partnership, "crr": crr,
        "captains": captains,
        "status": str((header.get("status") or header.get("state") or mini.get("status") or "LIVE")),
        "current_over": {"over": "", "balls": [], "free_hit": False, "last_ball": ""},
    }



def _public_page_score_fallback(match):
    """Last-resort generic fallback using the public Cricbuzz match page."""
    try:
        snap = entry.wsgi._scorecard_snapshot(match)
        if not isinstance(snap, dict):
            return None
        name_text = str(match.get("name") or "")
        try:
            t1, t2 = main.extract_teams(name_text)
        except Exception:
            parts = re.split(r"\\s+(?:vs|v|versus)\\s+", name_text, maxsplit=1, flags=re.I)
            t1 = parts[0].strip() if parts else ""
            t2 = parts[1].strip() if len(parts) > 1 else ""
        if not t1 or not t2:
            return None
        scores = snap.get("scores") or {}
        if not isinstance(scores, dict):
            scores = {}
        s1 = scores.get("team1") if isinstance(scores.get("team1"), dict) else None
        s2 = scores.get("team2") if isinstance(scores.get("team2"), dict) else None
        text = str(snap.get("text") or "")
        try:
            status_team = entry.wsgi._status_batting_team({"status": text}, t1, t2)
        except Exception:
            status_team = None
        if status_team:
            bi = 0 if entry.wsgi._team_matches(status_team, t1) else 1
        elif s1 and not s2:
            bi = 0
        elif s2 and not s1:
            bi = 1
        else:
            bi = 0
        result = {
            "title": f"{t1} vs {t2}", "url": match.get("url", ""),
            "team1": t1, "team2": t2,
            "team1_code": main.code(t1), "team2_code": main.code(t2),
            "team1_flag": main.flag(t1), "team2_flag": main.flag(t2),
            "team1_score": f"{s1.get('runs')}-{s1.get('wickets', 0)}" if s1 else "-",
            "team2_score": f"{s2.get('runs')}-{s2.get('wickets', 0)}" if s2 else "-",
            "team1_overs": str(s1.get("overs", "")) if s1 else "",
            "team2_overs": str(s2.get("overs", "")) if s2 else "",
            "crr": "-", "partnership": "-",
            "status": "LIVE / SCORECARD FALLBACK",
            "batsmen": [], "bowler": None,
            "captains": entry.wsgi._captains(match, t1, t2),
            "batting_index": bi, "bowling_index": 1 - bi,
            "current_over": {"over": "", "balls": [], "free_hit": False, "last_ball": ""},
        }
        return result if (s1 or s2) else None
    except Exception as exc:
        print("public page score fallback failed:", repr(exc))
        return None

def _fixed_selected_score():
    """Production score endpoint: never turn parser failures into HTTP 500."""
    mid = str(request.args.get("match_id", "")).strip()
    if not mid.isdigit():
        payload = {"match": None, "error": "match_id is required"}
        return Response(json.dumps(payload, default=str), mimetype="application/json", status=400,
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
    match = {"id": mid, "name": f"Match {mid}",
             "url": f"https://www.cricbuzz.com/live-cricket-scores/{mid}"}
    try:
        try:
            for item in get_matches():
                if str(item.get("id")) == mid:
                    match = item
                    break
        except Exception as exc:
            print("selected-score match discovery failed:", repr(exc))

        data = None
        try:
            data = entry.wsgi._extract_live(match)
        except Exception as exc:
            print("selected-score extract_live failed:", repr(exc))

        if data is None:
            try:
                data = _fallback_live(match)
            except Exception as exc:
                print("selected-score fallback failed:", repr(exc))

        if data is not None:
            try:
                data = entry._raw_score_fix(match, data)
            except Exception as exc:
                print("selected-score raw score fix skipped:", repr(exc))

        if data is None:
            try:
                data = _public_page_score_fallback(match)
            except Exception as exc:
                print("selected-score public page fallback skipped:", repr(exc))

        payload = {"match": data, "error": None if data else "live score temporarily unavailable"}
    except Exception as exc:
        print("selected-score outer failure:", repr(exc))
        payload = {"match": None, "error": "live score temporarily unavailable"}

    return Response(
        json.dumps(payload, default=str, ensure_ascii=False),
        mimetype="application/json",
        status=200,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "Vary": "*",
        },
    )


# Replace whichever /selected-score handler was installed by entry.py.
_selected_endpoint = next((r.endpoint for r in app.url_map.iter_rules() if r.rule == "/selected-score"), None)
if _selected_endpoint:
    app.view_functions[_selected_endpoint] = _fixed_selected_score
else:
    app.add_url_rule("/selected-score", endpoint="selector_selected_score", view_func=_fixed_selected_score, methods=["GET"])


def select_match():
    matches = get_matches()
    selected = str(request.args.get("selected", "")).strip()
    cards = []
    for m in matches:
        cards.append(f'''<div class="card"><div class="name">{main.escape_html(m["name"]) if hasattr(main, "escape_html") else m["name"]}</div><div class="id">Match ID: {m["id"]}</div><a class="btn" href="/select-match?selected={quote(str(m["id"]))}">SELECT THIS MATCH</a></div>''')
    chosen = next((m for m in matches if str(m["id"]) == selected), None)
    selected_html = ""
    if chosen:
        selected_html = f'''<div class="selected">✓ SELECTED: <b>{chosen["name"]}</b><br>Match ID: {chosen["id"]}<br><br><a href="/scoreboard?match_id={quote(str(chosen["id"]))}">OPEN LIVE SCOREBOARD</a></div>'''
    body = "".join(cards) or '<div class="empty">No live matches found. Tap REFRESH.</div>'
    html = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-store"><title>Cricket Match Selector</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#0b0f14;color:#fff;font-family:Arial,sans-serif;padding:18px}}h1{{font-size:24px;margin:4px 0 6px}}.sub{{color:#9aa4b2;font-size:13px;margin-bottom:14px}}.card{{background:#151c25;border:1px solid #293443;border-radius:13px;padding:15px;margin:10px 0}}.name{{font-size:17px;font-weight:700;line-height:1.3}}.id{{color:#8d99a8;font-size:12px;margin:7px 0 12px}}.btn{{display:block;text-align:center;background:#00c853;color:#001b0a;text-decoration:none;font-weight:800;padding:12px;border-radius:9px}}.refresh{{display:inline-block;background:#00c853;color:#001b0a;text-decoration:none;font-weight:800;padding:11px 15px;border-radius:9px;margin:0 0 12px}}.selected{{background:#122a1b;border:1px solid #00c853;padding:14px;border-radius:10px;margin:10px 0 15px}}.selected a{{display:inline-block;background:#00c853;color:#001b0a;padding:11px 14px;border-radius:8px;text-decoration:none;font-weight:900}}.empty{{padding:30px 10px;text-align:center;color:#aab3bf}}</style></head><body><h1>🏏 Cricket Match Selector</h1><div class="sub">Choose the live match to send to the OBS scoreboard.</div><a class="refresh" href="/select-match">↻ REFRESH MATCHES</a>{selected_html}{body}</body></html>'''
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


_select_endpoint = next((r.endpoint for r in app.url_map.iter_rules() if r.rule == "/select-match"), None)
if _select_endpoint:
    app.view_functions[_select_endpoint] = select_match
else:
    app.add_url_rule("/select-match", endpoint="select_match", view_func=select_match, methods=["GET"])


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))

def _selector_page():
    selected = str(request.args.get("selected", "")).strip()
    live_matches = get_matches()
    upcoming_matches = get_upcoming_matches([m["id"] for m in live_matches])
    if request.method == "POST":
        mid = str(request.form.get("match_id", "")).strip()
        if any(str(m["id"]) == mid for m in live_matches) or any(str(m["id"]) == mid for m in upcoming_matches):
            return __import__("flask").redirect("/select-match?selected=" + quote(mid))
    selected_live = next((m for m in live_matches if str(m["id"]) == selected), None)
    selected_upcoming = next((m for m in upcoming_matches if str(m["id"]) == selected), None)
    live_cards = [f'''<div class="card livecard"><div class="live">● LIVE / TODAY</div><div class="teams">{escape(m["name"])}</div><form method="POST"><input type="hidden" name="match_id" value="{escape(str(m["id"]))}"><button>SELECT THIS MATCH</button></form></div>''' for m in live_matches]
    if not live_cards: live_cards.append('<div class="empty">No live matches found right now. Tap refresh.</div>')
    upcoming_cards = []
    for m in upcoming_matches:
        extra = f'<div class="venue">{escape(m["venue"])}</div>' if m.get("venue") else ""
        upcoming_cards.append(f'''<div class="card upcoming"><div class="up">🕒 UPCOMING</div><div class="teams">{escape(m["name"])}</div><div class="date">{escape(m["date"])}</div>{extra}<form method="POST"><input type="hidden" name="match_id" value="{escape(str(m["id"]))}"><button class="blue">SELECT UPCOMING MATCH</button></form></div>''')
    if not upcoming_cards: upcoming_cards.append('<div class="empty">No upcoming matches found in the schedule feed right now.</div>')
    selected_html = ""
    if selected_live:
        board = "/scoreboard?match_id=" + quote(str(selected_live["id"]))
        selected_html = f'''<div class="selected"><b>✓ LIVE MATCH SELECTED</b><br><strong>{escape(selected_live["name"])}</strong><br>Match ID: {escape(str(selected_live["id"]))}<br><br><a href="{board}">OPEN LIVE SCOREBOARD</a></div>'''
    elif selected_upcoming:
        board = "/scoreboard?match_id=" + quote(str(selected_upcoming["id"]))
        selected_html = f'''<div class="selected upcoming-selected"><b>✓ UPCOMING MATCH SELECTED</b><br><strong>{escape(selected_upcoming["name"])}</strong><br>{escape(selected_upcoming["date"])}<br><br><a href="{board}">PREVIEW SCOREBOARD</a></div>'''
    html = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-store"><title>Cricket Match Selector</title><style>
body{{margin:0;background:#080d16;color:#fff;font-family:Arial,sans-serif}}.wrap{{max-width:950px;margin:auto;padding:20px 16px 50px}}h1{{margin:0 0 8px;font-size:27px}}p{{color:#b8c4d3}}h2{{margin-top:28px;border-bottom:1px solid #303b4a;padding-bottom:10px}}.card{{background:#141c28;border:1px solid #2c3949;border-radius:14px;padding:18px;margin:12px 0;box-shadow:0 5px 18px #0005}}.live{{color:#20e878;font-weight:900;font-size:13px}}.up{{color:#ffd21a;font-weight:900;font-size:13px}}.teams{{font-size:19px;font-weight:900;margin:10px 0 15px;line-height:1.35}}.date{{color:#ffd21a;font-weight:900}}.venue{{color:#aebccc;margin:7px 0 15px;font-size:14px}}button,a{{display:inline-block;padding:12px 16px;border:0;border-radius:8px;background:#00c853;color:#fff;font-weight:900;text-decoration:none;cursor:pointer}}.blue{{background:#1677ff}}.refresh{{background:#303947;margin:8px 0}}.selected{{background:#10351f;border:1px solid #00c853;border-radius:12px;padding:18px;line-height:1.7;margin:18px 0}}.upcoming-selected{{background:#162846;border-color:#1677ff}}.empty{{padding:20px;background:#141c28;border-radius:12px;color:#9da8b6}}.hint{{font-size:13px;color:#8794a5}}
</style></head><body><div class="wrap"><h1>🏏 CRICKET MATCH SELECTOR</h1><p>Select the match you want to send to the OBS scoreboard.</p>{selected_html}<form method="GET"><button class="refresh" type="submit">↻ REFRESH MATCHES</button></form><h2>🔴 Live Matches</h2>{''.join(live_cards)}<h2>🕒 Upcoming Matches</h2>{''.join(upcoming_cards)}<p class="hint">Upcoming matches are pulled automatically from Cricbuzz's schedule, so tomorrow's matches can appear without a code update.</p></div></body></html>'''
    return Response(html, mimetype="text/html")



def _fallback_live(match):
    """Build the exact scoreboard schema directly from Cricbuzz live JSON.
    This prevents the OBS page from being stuck at LOADING when the richer parser
    cannot resolve a scorecard snapshot or captain metadata.
    """
    mid = str(match["id"])
    url = str(match.get("url") or f"https://www.cricbuzz.com/live-cricket-scores/{mid}")
    match = dict(match)
    match["url"] = url
    live_url = f"https://www.cricbuzz.com/api/mcenter/comm/{mid}?obs_ts={time.time_ns()}"
    try:
        r = requests.get(live_url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print("fallback live fetch error:", repr(exc))
        return None

    mini = payload.get("miniscore") or {}
    header = payload.get("matchHeader") or {}
    t1 = main._team_name(header.get("team1")) if hasattr(main, "_team_name") else ""
    t2 = main._team_name(header.get("team2")) if hasattr(main, "_team2") else ""
    if not t1:
        t1 = (header.get("team1") or {}).get("teamName") or (header.get("team1") or {}).get("name") or "TEAM 1"
    if not t2:
        t2 = (header.get("team2") or {}).get("teamName") or (header.get("team2") or {}).get("name") or "TEAM 2"

    bat = mini.get("batTeam") or {}
    bat_obj = mini.get("batTeamScoreObj") or {}
    bat_name = bat_obj.get("teamName") or bat_obj.get("teamFullName") or bat_obj.get("name") or bat.get("teamName") or bat.get("teamShortName") or mini.get("batTeamName") or ""
    runs = bat.get("teamScore", bat.get("score", mini.get("teamScore")))
    wkts = bat.get("teamWkts", bat.get("wickets", mini.get("teamWkts")))
    overs = mini.get("overs", mini.get("oversStr", ""))
    if runs is None or wkts is None:
        return None

    def norm(v):
        return re.sub(r"[^a-z0-9]", "", str(v or "").lower())
    def team_match(a, b):
        a, b = norm(a), norm(b)
        return bool(a and b and (a == b or a in b or b in a))

    if team_match(bat_name, t1):
        bi = 0
    elif team_match(bat_name, t2):
        bi = 1
    else:
        bi = 0

    def player(obj, striker=False):
        if not isinstance(obj, dict):
            return None
        name = obj.get("batName") or obj.get("name") or obj.get("batsmanName")
        if not name:
            return None
        return {"name": str(name), "runs": str(obj.get("batRuns", obj.get("runs", obj.get("r", 0)))), "balls": str(obj.get("batBalls", obj.get("balls", obj.get("b", 0)))), "striker": bool(striker)}

    def bowler(obj):
        if not isinstance(obj, dict):
            return None
        name = obj.get("bowlName") or obj.get("name") or obj.get("bowlerName")
        if not name:
            return None
        return {"name": str(name), "overs": str(obj.get("bowlOvs", obj.get("overs", obj.get("o", "")))), "maidens": str(obj.get("bowlMaidens", obj.get("maidens", obj.get("m", 0)))), "runs": str(obj.get("bowlRuns", obj.get("runs", obj.get("r", 0)))), "wickets": str(obj.get("bowlWkts", obj.get("wickets", obj.get("w", 0)))), "economy": str(obj.get("bowlEcon", obj.get("economy", obj.get("eco", ""))))}

    bats = [x for x in [player(mini.get("batsmanStriker"), True), player(mini.get("batsmanNonStriker"), False)] if x]
    bo = bowler(mini.get("bowlerStriker") or mini.get("bowler") or mini.get("currentBowler"))
    partnership = mini.get("partnership") or mini.get("partnerShip") or mini.get("partnershipObj")
    if isinstance(partnership, dict):
        pr = partnership.get("runs", partnership.get("partnershipRuns", partnership.get("r")))
        pb = partnership.get("balls", partnership.get("partnershipBalls", partnership.get("b")))
        partnership = str(pr) + (f" ({pb})" if pb is not None else "") if pr is not None else "-"
    elif partnership is None:
        partnership = "-"
    else:
        partnership = str(partnership)

    try:
        crr = float(mini.get("currentRunRate", mini.get("crr")))
        crr = f"{crr:.2f}"
    except Exception:
        crr = str(mini.get("currentRunRate", mini.get("crr", "-")))

    flags = {"india": "🇮🇳", "westindies": "🌴", "australia": "🇦🇺", "england": "🏴", "pakistan": "🇵🇰", "southafrica": "🇿🇦", "srilanka": "🇱🇰", "bangladesh": "🇧🇩", "newzealand": "🇳🇿", "afghanistan": "🇦🇫"}
    def flag(team):
        return flags.get(norm(team), "🏳️")

    captains = []
    try:
        captains = entry.wsgi._captains(match, t1, t2) or []
    except Exception as exc:
        print("fallback captain parse error:", repr(exc))
    if len(captains) < 2:
        captains = (captains + [{"name": "Captain 1"}, {"name": "Captain 2"}])[:2]

    return {
        "title": f"{t1} vs {t2}", "url": url,
        "team1": t1, "team2": t2,
        "team1_code": main.code(t1), "team2_code": main.code(t2),
        "team1_flag": flag(t1), "team2_flag": flag(t2),
        "team1_score": f"{runs}-{wkts}" if bi == 0 else "-",
        "team2_score": f"{runs}-{wkts}" if bi == 1 else "-",
        "team1_overs": str(overs) if bi == 0 else "",
        "team2_overs": str(overs) if bi == 1 else "",
        "batting_index": bi, "bowling_index": 1 - bi,
        "batsmen": bats, "bowler": bo, "partnership": partnership, "crr": crr,
        "captains": captains,
        "status": str((header.get("status") or header.get("state") or mini.get("status") or "LIVE")),
        "current_over": {"over": "", "balls": [], "free_hit": False, "last_ball": ""},
    }


def _legacy_fixed_selected_score():
    mid = str(request.args.get("match_id", "")).strip()
    if not mid.isdigit():
        return jsonify({"match": None, "error": "match_id is required"}), 400
    match = next((m for m in get_matches() if str(m["id"]) == mid), None)
    if not match:
        match = {"id": mid, "name": f"Match {mid}", "url": f"https://www.cricbuzz.com/live-cricket-scores/{mid}"}
    try:
        data = entry.wsgi._extract_live(match)
        if data is None:
            data = _fallback_live(match)
        if data is not None:
            try:
                data = entry._raw_score_fix(match, data)
            except Exception as exc:
                print("raw score fix skipped:", repr(exc))
        response = jsonify({"match": data, "error": None if data else "live score unavailable"})
    except Exception as exc:
        print("selected-score fatal error:", repr(exc))
        data = _fallback_live(match)
        response = jsonify({"match": data, "error": None if data else "live score temporarily unavailable"})
    for k, v in {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0", "Vary": "*"}.items():
        response.headers[k] = v
    return response


# Replace whichever /selected-score handler was installed by entry.py.
_selected_endpoint = next((r.endpoint for r in app.url_map.iter_rules() if r.rule == "/selected-score"), None)
if _selected_endpoint:
    app.view_functions[_selected_endpoint] = _fixed_selected_score
else:
    app.add_url_rule("/selected-score", endpoint="selector_selected_score", view_func=_fixed_selected_score, methods=["GET"])


def select_match():
    matches = get_matches()
    selected = str(request.args.get("selected", "")).strip()
    cards = []
    for m in matches:
        cards.append(f'''<div class="card"><div class="name">{main.escape_html(m["name"]) if hasattr(main, "escape_html") else m["name"]}</div><div class="id">Match ID: {m["id"]}</div><a class="btn" href="/select-match?selected={quote(str(m["id"]))}">SELECT THIS MATCH</a></div>''')
    chosen = next((m for m in matches if str(m["id"]) == selected), None)
    selected_html = ""
    if chosen:
        selected_html = f'''<div class="selected">✓ SELECTED: <b>{chosen["name"]}</b><br>Match ID: {chosen["id"]}<br><br><a href="/scoreboard?match_id={quote(str(chosen["id"]))}">OPEN LIVE SCOREBOARD</a></div>'''
    body = "".join(cards) or '<div class="empty">No live matches found. Tap REFRESH.</div>'
    html = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-store"><title>Cricket Match Selector</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#0b0f14;color:#fff;font-family:Arial,sans-serif;padding:18px}}h1{{font-size:24px;margin:4px 0 6px}}.sub{{color:#9aa4b2;font-size:13px;margin-bottom:14px}}.card{{background:#151c25;border:1px solid #293443;border-radius:13px;padding:15px;margin:10px 0}}.name{{font-size:17px;font-weight:700;line-height:1.3}}.id{{color:#8d99a8;font-size:12px;margin:7px 0 12px}}.btn{{display:block;text-align:center;background:#00c853;color:#001b0a;text-decoration:none;font-weight:800;padding:12px;border-radius:9px}}.refresh{{display:inline-block;background:#00c853;color:#001b0a;text-decoration:none;font-weight:800;padding:11px 15px;border-radius:9px;margin:0 0 12px}}.selected{{background:#122a1b;border:1px solid #00c853;padding:14px;border-radius:10px;margin:10px 0 15px}}.selected a{{display:inline-block;background:#00c853;color:#001b0a;padding:11px 14px;border-radius:8px;text-decoration:none;font-weight:900}}.empty{{padding:30px 10px;text-align:center;color:#aab3bf}}</style></head><body><h1>🏏 Cricket Match Selector</h1><div class="sub">Choose the live match to send to the OBS scoreboard.</div><a class="refresh" href="/select-match">↻ REFRESH MATCHES</a>{selected_html}{body}</body></html>'''
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


_select_endpoint = next((r.endpoint for r in app.url_map.iter_rules() if r.rule == "/select-match"), None)
if _select_endpoint:
    app.view_functions[_select_endpoint] = select_match
else:
    app.add_url_rule("/select-match", endpoint="select_match", view_func=select_match, methods=["GET"])


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
# Use the automatic live + upcoming selector in production. It is deliberately
# assigned after the page function is defined so the older selector cannot win.
_select_page_endpoint = next((r.endpoint for r in app.url_map.iter_rules() if r.rule == "/select-match"), None)
if _select_page_endpoint:
    app.view_functions[_select_page_endpoint] = _selector_page

# The original /select-match route in main.py is GET-only. The production
# selector uses POST forms for selecting a match, so explicitly allow POST
# on that existing rule as well as GET.
for _rule in app.url_map.iter_rules():
    if _rule.rule == "/select-match":
        _rule.methods = frozenset(set(_rule.methods or ()) | {"GET", "POST", "HEAD", "OPTIONS"})
