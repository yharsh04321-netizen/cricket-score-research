# Render production entrypoint for the OBS cricket scoreboard.
import time
import re
import requests
from bs4 import BeautifulSoup
from html import escape
from urllib.parse import quote
import wsgi
from flask import request, jsonify, send_from_directory, Response

app = wsgi.app

# Keep the latest raw Cricbuzz response fresh. Cricbuzz's live score is a
# polling endpoint; the site itself refreshes this data about every 5 seconds.
SCORE_MEMORY = {}


def _obs_fresh_live_data(match):
    mid = wsgi._match_id(match)
    if not mid:
        return None
    url = wsgi.LIVE_URL.format(mid) + "?obs_ts=" + str(time.time_ns())
    headers = dict(wsgi.HEADERS)
    headers.update({
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
        "User-Agent": "Mozilla/5.0 (cricket-live-overlay/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.cricbuzz.com/",
        "Origin": "https://www.cricbuzz.com",
    })
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        wsgi.LIVE_CACHE[mid] = {"time": time.time(), "data": data}
        return data
    except Exception as exc:
        print("fresh live center error:", repr(exc))
        cached = wsgi.LIVE_CACHE.get(mid)
        return cached["data"] if cached else None


wsgi._live_data = _obs_fresh_live_data


def _valid_score(v):
    return v not in (None, "", "-")


def _raw_score_fix(match, data):
    """Fill score fields directly from miniscore.batTeam."""
    if not isinstance(data, dict):
        return data
    payload = _obs_fresh_live_data(match)
    if not isinstance(payload, dict):
        return data
    mini = payload.get("miniscore") or {}
    bat = mini.get("batTeam") or {}
    header = payload.get("matchHeader") or {}
    t1obj, t2obj = header.get("team1") or {}, header.get("team2") or {}
    t1 = wsgi._team_name(t1obj) or data.get("team1", "")
    t2 = wsgi._team_name(t2obj) or data.get("team2", "")
    bat_name = (wsgi._team_name(bat)
                or wsgi._team_name(mini.get("batTeamScoreObj") or {})
                or mini.get("batTeamName")
                or mini.get("batTeamShortName")
                or "")
    runs = bat.get("teamScore", bat.get("score", mini.get("teamScore")))
    wickets = bat.get("teamWkts", bat.get("wickets", mini.get("teamWkts")))
    overs = mini.get("overs", mini.get("oversStr", ""))

    # _extract_live already resolves innings-break/result cases using the
    # strongest available signals (header ID, status text, and live team).
    # Preserve that decision here instead of letting a stale currBatTeamId
    # undo it during the final OBS score response.
    idx = data.get("batting_index", None)
    try:
        idx = int(idx) if idx is not None else None
    except Exception:
        idx = None
    if idx not in (0, 1):
        idx = None

    if idx is None:
        curr_id = header.get("currBatTeamId") or header.get("currentBatTeamId")
        if curr_id not in (None, ""):
            for candidate, obj in ((0, t1obj), (1, t2obj)):
                tid = (obj.get("teamId") or obj.get("id")) if isinstance(obj, dict) else None
                if tid not in (None, "") and str(tid) == str(curr_id):
                    idx = candidate
                    break

    if idx is None:
        if wsgi._team_matches(bat_name, t1):
            idx = 0
        elif wsgi._team_matches(bat_name, t2):
            idx = 1
        else:
            idx = 0

    # If miniscore has no usable total, leave the richer wsgi result
    # untouched. _extract_live may have recovered it from status/header/CRR.
    if runs is None or wickets is None or str(runs).strip() in {"", "-", "—"} or str(wickets).strip() in {"", "-", "—"}:
        data["team1"] = t1 or data.get("team1", "TEAM 1")
        data["team2"] = t2 or data.get("team2", "TEAM 2")
        data["batting_index"] = idx
        data["bowling_index"] = 1 - idx
        return data

    # If the rich parser resolved a different batting side from the raw
    # miniscore, the raw miniscore can be a previous-innings snapshot during
    # result/innings-break states. In that case never overwrite the richer
    # result with stale runs/wickets.
    resolved_bat = str(data.get("batting_team") or "").strip()
    if resolved_bat and bat_name and not wsgi._team_matches(resolved_bat, bat_name):
        data["team1"] = t1 or data.get("team1", "TEAM 1")
        data["team2"] = t2 or data.get("team2", "TEAM 2")
        data["bowling_index"] = 1 - idx
        return data

    score = f"{runs}-{wickets}"

    key = str(wsgi._match_id(match))
    mem = SCORE_MEMORY.setdefault(key, {"team1_score": "-", "team2_score": "-", "team1_overs": "", "team2_overs": ""})
    if idx == 0:
        mem["team1_score"] = score
        if overs != "": mem["team1_overs"] = str(overs)
    else:
        mem["team2_score"] = score
        if overs != "": mem["team2_overs"] = str(overs)

    if not _valid_score(data.get("team1_score")):
        data["team1_score"] = mem["team1_score"]
    if not _valid_score(data.get("team2_score")):
        data["team2_score"] = mem["team2_score"]
    if not data.get("team1_overs"):
        data["team1_overs"] = mem["team1_overs"]
    if not data.get("team2_overs"):
        data["team2_overs"] = mem["team2_overs"]
    if not _valid_score(data.get("team1_score")) and idx == 0:
        data["team1_score"] = score
    if not _valid_score(data.get("team2_score")) and idx == 1:
        data["team2_score"] = score

    data["team1"] = t1 or data.get("team1", "TEAM 1")
    data["team2"] = t2 or data.get("team2", "TEAM 2")
    data["batting_index"] = idx
    data["bowling_index"] = 1 - idx
    return data


def _obs_selected_score():
    mid = str(request.args.get("match_id", "")).strip()
    if not mid.isdigit():
        return jsonify({"match": None, "error": "match_id is required"}), 400
    try:
        match = wsgi.main.get_match_by_id(mid)
        data = wsgi._extract_live(match)
        if data is None:
            data = wsgi._fetch_match_detail(match)
        data = _raw_score_fix(match, data)
        response = jsonify({"match": data})
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Vary"] = "*"
        return response
    except Exception as exc:
        print("OBS selected-score error:", repr(exc))
        response = jsonify({"match": None, "error": "live score temporarily unavailable"})
        response.status_code = 200
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


_selected_endpoint = None
for _rule in app.url_map.iter_rules():
    if _rule.rule == "/selected-score":
        _selected_endpoint = _rule.endpoint
        break
if _selected_endpoint:
    app.view_functions[_selected_endpoint] = _obs_selected_score
else:
    app.add_url_rule("/selected-score", endpoint="selected_score", view_func=_obs_selected_score, methods=["GET"])


def _obs_scoreboard():
    return send_from_directory("static", "scoreboard_full_v2.html")

for _rule in list(app.url_map.iter_rules()):
    if _rule.rule == "/scoreboard":
        app.view_functions[_rule.endpoint] = _obs_scoreboard
        break


# ---------------------------------------------------------------------------
# LIVE MATCH SELECTOR
# ---------------------------------------------------------------------------
# The old selector only displayed a hard-coded selected ID. This page discovers
# current Cricbuzz live-score links and lets the user select one before opening
# the OBS scoreboard. The scoreboard/live-data code above remains unchanged.

def _discover_live_matches():
    matches = []
    seen = set()
    try:
        url = "https://www.cricbuzz.com/cricket-match/live-scores"
        r = requests.get(url, headers=wsgi.HEADERS, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            m = re.search(r"/live-cricket-scores/(\d+)/([^\"?#]+)", href)
            if not m:
                continue
            mid, slug = m.group(1), m.group(2)
            if mid in seen:
                continue
            text = " ".join(a.stripped_strings)
            if not text:
                text = slug.replace("-", " ").title()
            text = re.sub(r"\s+", " ", text).strip()
            # Avoid navigation links that happen to contain a score URL.
            if len(text) < 5 or "scorecard" in text.lower():
                continue
            seen.add(mid)
            matches.append({
                "id": mid,
                "name": text,
                "url": "https://www.cricbuzz.com/live-cricket-scores/" + mid + "/" + slug,
            })
    except Exception as exc:
        print("selector discovery error:", repr(exc))

    # Keep the current India-West Indies match available if Cricbuzz's HTML
    # temporarily omits it from the live-score index.
    if not any(str(x["id"]) == "151543" for x in matches):
        matches.insert(0, {
            "id": "151543",
            "name": "India vs West Indies — 2nd ODI",
            "url": "https://www.cricbuzz.com/live-cricket-scores/151543/ind-vs-wi-2nd-odi-india-v-west-indies",
        })
    return matches


UPCOMING_SELECTOR = [
    ("upcoming-ind-wi-3odi", "India 🇮🇳 vs West Indies 🌴 — 3rd ODI", "03 Oct 2026", "2:00 PM IST", "PCA International Cricket Stadium, New Chandigarh"),
    ("upcoming-ind-wi-1t20", "India 🇮🇳 vs West Indies 🌴 — 1st T20I", "06 Oct 2026", "7:00 PM IST", "Ekana Cricket Stadium, Lucknow"),
    ("upcoming-ind-wi-2t20", "India 🇮🇳 vs West Indies 🌴 — 2nd T20I", "09 Oct 2026", "7:00 PM IST", "JSCA International Stadium, Ranchi"),
    ("upcoming-ind-wi-3t20", "India 🇮🇳 vs West Indies 🌴 — 3rd T20I", "11 Oct 2026", "7:00 PM IST", "Holkar Stadium, Indore"),
    ("upcoming-ind-wi-4t20", "India 🇮🇳 vs West Indies 🌴 — 4th T20I", "14 Oct 2026", "7:00 PM IST", "Rajiv Gandhi International Stadium, Hyderabad"),
    ("upcoming-ind-wi-5t20", "India 🇮🇳 vs West Indies 🌴 — 5th T20I", "17 Oct 2026", "7:00 PM IST", "M Chinnaswamy Stadium, Bengaluru"),
]


def _selector_page():
    selected = str(request.args.get("selected", "")).strip()
    live_matches = _discover_live_matches()

    if request.method == "POST":
        mid = str(request.form.get("match_id", "")).strip()
        valid = any(str(m["id"]) == mid for m in live_matches) or any(x[0] == mid for x in UPCOMING_SELECTOR)
        if valid:
            return __import__("flask").redirect("/select-match?selected=" + quote(mid))

    selected_live = next((m for m in live_matches if str(m["id"]) == selected), None)
    selected_upcoming = next((x for x in UPCOMING_SELECTOR if x[0] == selected), None)

    live_cards = []
    for m in live_matches:
        live_cards.append(f'''<div class="card livecard"><div class="live">● LIVE / TODAY</div><div class="teams">{escape(m["name"])}</div><form method="POST"><input type="hidden" name="match_id" value="{escape(str(m["id"]))}"><button>SELECT THIS MATCH</button></form></div>''')
    if not live_cards:
        live_cards.append('<div class="empty">No live matches found right now. Tap refresh.</div>')

    upcoming_cards = []
    for mid, name, date, tm, venue in UPCOMING_SELECTOR:
        upcoming_cards.append(f'''<div class="card upcoming"><div class="up">🕒 UPCOMING</div><div class="teams">{escape(name)}</div><div class="date">{escape(date)} · {escape(tm)}</div><div class="venue">{escape(venue)}</div><form method="POST"><input type="hidden" name="match_id" value="{escape(mid)}"><button class="blue">SELECT UPCOMING MATCH</button></form></div>''')

    selected_html = ""
    if selected_live:
        board = "/scoreboard?match_id=" + quote(str(selected_live["id"]))
        selected_html = f'''<div class="selected"><b>✓ LIVE MATCH SELECTED</b><br><strong>{escape(selected_live["name"])}</strong><br><span>Match ID: {escape(str(selected_live["id"]))}</span><br><br><a href="{board}">OPEN LIVE SCOREBOARD</a></div>'''
    elif selected_upcoming:
        board = "/scoreboard?match_id=" + quote(selected_upcoming[0])
        selected_html = f'''<div class="selected upcoming-selected"><b>✓ UPCOMING MATCH SELECTED</b><br><strong>{escape(selected_upcoming[1])}</strong><br>{escape(selected_upcoming[2])} · {escape(selected_upcoming[3])}<br><br><a href="{board}">PREVIEW SCOREBOARD</a></div>'''

    html = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-store"><title>Cricket Match Selector</title><style>
body{{margin:0;background:#080d16;color:#fff;font-family:Arial,sans-serif}}.wrap{{max-width:950px;margin:auto;padding:20px 16px 50px}}h1{{margin:0 0 8px;font-size:27px}}p{{color:#b8c4d3}}h2{{margin-top:28px;border-bottom:1px solid #303b4a;padding-bottom:10px}}.card{{background:#141c28;border:1px solid #2c3949;border-radius:14px;padding:18px;margin:12px 0;box-shadow:0 5px 18px #0005}}.live{{color:#20e878;font-weight:900;font-size:13px}}.up{{color:#ffd21a;font-weight:900;font-size:13px}}.teams{{font-size:19px;font-weight:900;margin:10px 0 15px;line-height:1.35}}.date{{color:#ffd21a;font-weight:900}}.venue{{color:#aebccc;margin:7px 0 15px;font-size:14px}}button,a{{display:inline-block;padding:12px 16px;border:0;border-radius:8px;background:#00c853;color:#fff;font-weight:900;text-decoration:none;cursor:pointer}}.blue{{background:#1677ff}}.refresh{{background:#303947;margin:8px 0}}.selected{{background:#10351f;border:1px solid #00c853;border-radius:12px;padding:18px;line-height:1.7;margin:18px 0}}.upcoming-selected{{background:#162846;border-color:#1677ff}}.empty{{padding:20px;background:#141c28;border-radius:12px;color:#9da8b6}}.hint{{font-size:13px;color:#8794a5}}
</style></head><body><div class="wrap"><h1>🏏 CRICKET MATCH SELECTOR</h1><p>Select the match you want to send to the OBS scoreboard.</p>{selected_html}<form method="GET"><button class="refresh" type="submit">↻ REFRESH LIVE MATCHES</button></form><h2>🔴 Live Matches</h2>{''.join(live_cards)}<h2>🕒 Upcoming Matches</h2>{''.join(upcoming_cards)}<p class="hint">After selecting a match, open its scoreboard and use that page as the OBS Browser Source.</p></div></body></html>'''
    return Response(html, mimetype="text/html")


# Replace the old /select-match route with the real selector.
for _rule in list(app.url_map.iter_rules()):
    if _rule.rule == "/select-match":
        app.view_functions[_rule.endpoint] = _selector_page
        break


@app.after_request
def _obs_live_no_cache(response):
    if request.path in ("/scoreboard", "/selected-score", "/select-match"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Vary"] = "*"
    return response
