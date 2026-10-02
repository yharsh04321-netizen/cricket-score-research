import main
import livefix
from flask import Response, request, redirect
from html import escape
from urllib.parse import quote

UPCOMING = [
    {"id":"up_3odi","name":"India vs West Indies - 3rd ODI","date":"Saturday, 03 October 2026","time":"2:00 PM IST","venue":"PCA International Cricket Stadium, New Chandigarh","url":"https://www.windiescricket.com/series/west-indies-in-india-2026-27-22034/"},
    {"id":"up_1t20i","name":"India vs West Indies - 1st T20I","date":"Tuesday, 06 October 2026","time":"7:00 PM IST","venue":"Lucknow","url":"https://www.windiescricket.com/series/west-indies-in-india-2026-27-22034/"},
    {"id":"up_2t20i","name":"India vs West Indies - 2nd T20I","date":"Friday, 09 October 2026","time":"7:00 PM IST","venue":"Ranchi","url":"https://www.windiescricket.com/series/west-indies-in-india-2026-27-22034/"},
]

for m in UPCOMING:
    m["url"] = m["url"]

_original_get = main.get_match_by_id

def get_match_by_id(match_id):
    sid = str(match_id or "")
    found = next((m for m in UPCOMING if m["id"] == sid), None)
    if found:
        return dict(found)
    return _original_get(match_id)

main.get_match_by_id = get_match_by_id

_original_select = main.app.view_functions.get("select_match")

def select_match():
    live_matches = main.fetch_matches()
    selected_id = request.args.get("selected", "")
    selected = next((m for m in live_matches if str(m["id"]) == str(selected_id)), None)
    upcoming_selected = next((m for m in UPCOMING if m["id"] == selected_id), None)

    if request.method == "POST":
        mid = request.form.get("match_id", "")
        if any(str(m["id"]) == str(mid) for m in live_matches) or any(m["id"] == mid for m in UPCOMING):
            return redirect("/select-match?selected=" + quote(str(mid)))

    live_cards = "".join(f'''<div class="match live-card"><div class="live">● LIVE / TODAY</div><div class="name">{escape(m["name"])}</div><form method="POST"><input type="hidden" name="match_id" value="{escape(str(m["id"]))}"><button>SELECT THIS MATCH</button></form></div>''' for m in live_matches)
    upcoming_cards = "".join(f'''<div class="match upcoming-card"><div class="upcoming">🕒 UPCOMING</div><div class="name">{escape(m["name"])}</div><div class="details">📅 {escape(m["date"])} &nbsp; • &nbsp; 🕐 {escape(m["time"])}<br>📍 {escape(m["venue"])}</div><form method="POST"><input type="hidden" name="match_id" value="{escape(m["id"])}"><button class="upbtn">SELECT UPCOMING MATCH</button></form></div>''' for m in UPCOMING)

    selected_html = ""
    selected_match = selected or upcoming_selected
    if selected_match:
        board = "/scoreboard?match_id=" + quote(str(selected_match["id"]))
        selected_html = f'''<div class="selected">✓ SELECTED MATCH<br><br><strong>{escape(selected_match["name"])}</strong><br><br><a href="{escape(board)}">OPEN SCOREBOARD</a></div>'''

    if not live_cards:
        live_cards = '<div class="empty">No current live matches found right now.</div>'

    html = f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cricket Match Selector</title><style>body{{margin:0;background:#101010;color:#fff;font-family:Arial}}.container{{max-width:900px;margin:20px auto;padding:20px}}h1{{margin-bottom:5px}}h2{{margin-top:30px}}.match{{background:#1d1d1d;border:1px solid #333;border-radius:12px;padding:20px;margin-bottom:15px}}.live{{color:#00e676;font-size:13px;font-weight:bold;margin-bottom:10px}}.upcoming{{color:#ffd21a;font-size:13px;font-weight:bold;margin-bottom:10px}}.name{{font-size:19px;font-weight:bold;margin-bottom:12px}}.details{{color:#bbb;font-size:14px;line-height:1.8;margin-bottom:15px}}button,a{{background:#00c853;color:#fff;border:0;border-radius:7px;padding:12px 20px;font-weight:bold;text-decoration:none;display:inline-block}}.upbtn{{background:#e0a800;color:#111}}.refresh{{background:#333;margin-bottom:20px}}.selected{{background:#12351f;border:1px solid #00c853;border-radius:10px;padding:20px;margin-bottom:20px}}.empty{{background:#1d1d1d;padding:30px;text-align:center;color:#aaa;border-radius:10px}}</style></head><body><div class="container"><h1>🏏 CRICKET MATCH SELECTOR</h1><p>Choose a live match or an upcoming match for your OBS scoreboard.</p>{selected_html}<button class="refresh" onclick="location.href='/select-match'">↻ REFRESH MATCHES</button><h2>🔴 Live Matches</h2>{live_cards}<h2>🕒 Upcoming Matches</h2>{upcoming_cards}</div></body></html>'''
    return Response(html, mimetype="text/html")

main.app.view_functions["select_match"] = select_match
app = main.app
