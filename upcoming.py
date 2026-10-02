from flask import Response, request
from html import escape
from urllib.parse import quote
from datetime import datetime

# Keep the existing live system from livefix.py completely intact.
import livefix
import main

app = livefix.app

UPCOMING = [
    {"id":"upcoming-ind-wi-3odi","name":"India vs West Indies - 3rd ODI","short":"INDIA 🇮🇳 vs WEST INDIES 🌴","date":"2026-10-03","time":"14:00 IST","venue":"PCA International Cricket Stadium, New Chandigarh","team1":"India","team2":"West Indies","flag1":"🇮🇳","flag2":"🌴"},
    {"id":"upcoming-ind-wi-1t20","name":"India vs West Indies - 1st T20I","short":"INDIA 🇮🇳 vs WEST INDIES 🌴","date":"2026-10-06","time":"19:00 IST","venue":"Bharat Ratna Shri Atal Bihari Vajpayee Ekana Cricket Stadium, Lucknow","team1":"India","team2":"West Indies","flag1":"🇮🇳","flag2":"🌴"},
    {"id":"upcoming-ind-wi-2t20","name":"India vs West Indies - 2nd T20I","short":"INDIA 🇮🇳 vs WEST INDIES 🌴","date":"2026-10-09","time":"19:00 IST","venue":"JSCA International Stadium Complex, Ranchi","team1":"India","team2":"West Indies","flag1":"🇮🇳","flag2":"🌴"},
    {"id":"upcoming-ind-wi-3t20","name":"India vs West Indies - 3rd T20I","short":"INDIA 🇮🇳 vs WEST INDIES 🌴","date":"2026-10-11","time":"19:00 IST","venue":"Holkar Stadium, Indore","team1":"India","team2":"West Indies","flag1":"🇮🇳","flag2":"🌴"},
    {"id":"upcoming-ind-wi-4t20","name":"India vs West Indies - 4th T20I","short":"INDIA 🇮🇳 vs WEST INDIES 🌴","date":"2026-10-14","time":"19:00 IST","venue":"Rajiv Gandhi International Stadium, Hyderabad","team1":"India","team2":"West Indies","flag1":"🇮🇳","flag2":"🌴"},
    {"id":"upcoming-ind-wi-5t20","name":"India vs West Indies - 5th T20I","short":"INDIA 🇮🇳 vs WEST INDIES 🌴","date":"2026-10-17","time":"19:00 IST","venue":"M Chinnaswamy Stadium, Bengaluru","team1":"India","team2":"West Indies","flag1":"🇮🇳","flag2":"🌴"},
]


def upcoming_by_id(mid):
    return next((m for m in UPCOMING if m["id"] == str(mid)), None)


def upcoming_match_detail(m):
    return {
        "title": m["name"], "url": "", "team1": m["team1"], "team2": m["team2"],
        "team1_code": "IND", "team2_code": "WI", "team1_flag": m["flag1"], "team2_flag": m["flag2"],
        "team1_score": "-", "team2_score": "-", "team1_overs": "", "team2_overs": "",
        "crr": "-", "partnership": "-", "status": "UPCOMING - " + m["date"] + " " + m["time"],
        "batsmen": [], "bowler": None, "captains": []
    }


def select_match_upcoming():
    live_matches = main.fetch_matches()
    selected_id = request.args.get("selected", "")
    selected_live = next((m for m in live_matches if str(m["id"]) == str(selected_id)), None)
    selected_upcoming = upcoming_by_id(selected_id)

    if request.method == "POST":
        mid = request.form.get("match_id", "")
        if any(str(m["id"]) == str(mid) for m in live_matches) or upcoming_by_id(mid):
            return main.redirect("/select-match?selected=" + quote(str(mid)))

    live_cards = "".join(
        f'''<div class="match live-card"><div class="live">● LIVE / TODAY</div><div class="name">{escape(m["name"])}</div><form method="POST"><input type="hidden" name="match_id" value="{escape(m["id"])}"><button>SELECT THIS MATCH</button></form></div>'''
        for m in live_matches
    )

    upcoming_cards = "".join(
        f'''<div class="match upcoming-card"><div class="upcoming">🕒 UPCOMING</div><div class="teams"><span>{escape(m["flag1"])}</span> {escape(m["team1"])} <b>VS</b> {escape(m["team2"])} {escape(m["flag2"])}</div><div class="date">{escape(m["date"])} · {escape(m["time"])}</div><div class="venue">{escape(m["venue"])}</div><form method="POST"><input type="hidden" name="match_id" value="{escape(m["id"])}"><button class="upbtn">SELECT UPCOMING MATCH</button></form></div>'''
        for m in UPCOMING
    )

    selected_html = ""
    if selected_live:
        board = "/scoreboard?match_id=" + quote(str(selected_live["id"]))
        selected_html = f'''<div class="selected"><div class="ok">✓ SELECTED LIVE MATCH</div><strong>{escape(selected_live["name"])}</strong><br><br>OBS scoreboard URL:<br><code>{escape(board)}</code><br><br><a href="{escape(board)}">OPEN SCOREBOARD</a></div>'''
    elif selected_upcoming:
        board = "/scoreboard?match_id=" + quote(selected_upcoming["id"])
        selected_html = f'''<div class="selected upcoming-selected"><div class="ok">✓ UPCOMING MATCH SELECTED</div><strong>{escape(selected_upcoming["short"])}</strong><br><span>{escape(selected_upcoming["date"])} · {escape(selected_upcoming["time"])}</span><br><span>{escape(selected_upcoming["venue"])}</span><br><br><a href="{escape(board)}">PREVIEW SCOREBOARD</a><p class="note">When this match becomes live, the live-score system will take over automatically when its live match ID is available.</p></div>'''

    html = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cricket Match Selector</title><style>body{{margin:0;background:#080d16;color:#fff;font-family:Arial,sans-serif}}.container{{max-width:950px;margin:0 auto;padding:25px 18px 50px}}h1{{font-size:30px;margin-bottom:6px}}h2{{margin-top:32px;border-bottom:1px solid #303b4a;padding-bottom:10px}}.match{{border-radius:14px;padding:20px;margin:14px 0;border:1px solid #2a3442;background:#141c28;box-shadow:0 5px 18px #0005}}.live{{color:#20e878;font-size:13px;font-weight:900;margin-bottom:10px}}.upcoming{{color:#ffd21a;font-size:13px;font-weight:900;margin-bottom:10px}}.name{{font-size:20px;font-weight:900;margin-bottom:16px}}.teams{{font-size:22px;font-weight:900;margin-bottom:10px}}.teams b{{color:#ffd21a;margin:0 10px}}.date{{font-size:17px;font-weight:800;color:#ffd21a}}.venue{{font-size:14px;color:#b8c4d3;margin:7px 0 16px}}button,a{{background:#00c853;color:#fff;border:0;border-radius:8px;padding:12px 18px;font-weight:900;text-decoration:none;display:inline-block;cursor:pointer}}.upbtn{{background:#1677ff}}.refresh{{background:#303947;margin-bottom:5px}}.selected{{background:#10351f;border:1px solid #00c853;border-radius:12px;padding:20px;margin:20px 0;line-height:1.7}}.upcoming-selected{{background:#162846;border-color:#1677ff}}.ok{{color:#00e676;font-weight:900}}code{{background:#000;padding:6px;border-radius:5px;word-break:break-all}}.note{{color:#b9c6d6;font-size:13px;margin-bottom:0}}.empty{{padding:25px;color:#9da8b6;background:#141c28;border-radius:12px}}</style></head><body><div class="container"><h1>🏏 CRICKET MATCH SELECTOR</h1><p>Select a live match or prepare an upcoming match for the OBS overlay.</p>{selected_html}<form method="GET"><button class="refresh" type="submit">↻ REFRESH MATCHES</button></form><h2>🔴 Live Matches</h2>{live_cards if live_cards else '<div class="empty">No live matches found right now.</div>'}<h2>🕒 Upcoming Matches</h2>{upcoming_cards}</div></body></html>'''
    return Response(html, mimetype="text/html")


# Replace only the selector route. The existing live-score and scoreboard logic stays unchanged.
app.view_functions["select_match"] = select_match_upcoming

# Make the existing selected-score endpoint understand our upcoming preview IDs.
_original_selected_score = app.view_functions.get("selected_score")

def selected_score_upcoming():
    mid = request.args.get("match_id", "")
    m = upcoming_by_id(mid)
    if m:
        return main.jsonify({"success": True, "selected": True, "match": upcoming_match_detail(m)})
    return _original_selected_score()

if _original_selected_score:
    app.view_functions["selected_score"] = selected_score_upcoming

# Make /scoreboard work for upcoming preview IDs without changing live matches.
_original_scoreboard = app.view_functions.get("scoreboard")

def scoreboard_upcoming():
    mid = request.args.get("match_id", "")
    m = upcoming_by_id(mid)
    if not m:
        return _original_scoreboard()
    # Reuse the normal scoreboard template by temporarily translating the ID
    # to a synthetic match object through a tiny endpoint-compatible shim.
    original_get = main.get_match_by_id
    original_detail = main.fetch_match_detail
    try:
        main.get_match_by_id = lambda x: upcoming_match_detail(m) if str(x) == m["id"] else original_get(x)
        main.fetch_match_detail = lambda x: upcoming_match_detail(m) if str(x.get("id")) == m["id"] else original_detail(x)
        return _original_scoreboard()
    finally:
        main.get_match_by_id = original_get
        main.fetch_match_detail = original_detail

if _original_scoreboard:
    app.view_functions["scoreboard"] = scoreboard_upcoming
