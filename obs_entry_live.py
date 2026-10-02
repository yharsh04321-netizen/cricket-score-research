from flask import request, Response
import re, html
from pathlib import Path
import selector_entry, main, wsgi

app = selector_entry.app
EMPTY = {"over":"","balls":[],"free_hit":False,"last_ball":""}

# The live commentary endpoint can remain on the last live snapshot after a
# match finishes. The scorecard page is authoritative for the final result.
def _scorecard_url(url):
    u = str(url or "")
    u = u.replace("/live-cricket-scores/", "/live-cricket-scorecard/")
    if "/live-cricket-scorecard/" not in u:
        m = re.search(r"/(\d+)(?:/[^?#]*)?", u)
        if m:
            return "https://www.cricbuzz.com/live-cricket-scorecard/" + m.group(1)
    return u


def _team_code(name):
    n = " ".join(str(name or "").split()).strip().upper()
    aliases = {
        "INDIA":"IND", "WEST INDIES":"WI", "AUSTRALIA":"AUS",
        "SOUTH AFRICA":"RSA", "ENGLAND":"ENG", "SRI LANKA":"SL",
        "PAKISTAN":"PAK", "BANGLADESH":"BAN", "AFGHANISTAN":"AFG",
        "NEW ZEALAND":"NZ", "ZIMBABWE":"ZIM", "IRELAND":"IRE",
    }
    return aliases.get(n, n[:5] or "T1")


def _team_flag(name):
    n = str(name or "").lower()
    for key, value in main.FLAGS.items():
        if key in n:
            return value
    return "🏳️"


def _extract_teams(title):
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    parts = re.split(r"\s+vs\s+|\s+v\s+", text, flags=re.I)
    if len(parts) >= 2:
        return parts[0].strip(), re.split(r"\s+-\s+|\s+\|\s+|\s+\(\s*", parts[1])[0].strip()
    return "TEAM 1", "TEAM 2"


def _parse_scores(text):
    out = []
    # Cricbuzz scorecard text normally contains e.g. IND 406-2 (43.3 Ov).
    pat = re.compile(r"\b([A-Z]{2,5})\s+(\d+)\s*[-/]\s*(\d+)\s*\((\d+(?:\.\d+)?)\s*(?:Ov|Overs)\)", re.I)
    seen = set()
    for code, runs, wickets, overs in pat.findall(str(text or "")):
        key = (code.upper(), runs, wickets, overs)
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": code.upper(), "runs": runs, "wickets": wickets, "overs": overs})
    return out


def _parse_captains(_html, _text):
    return []


# wsgi.py expects these helpers. Supplying them here keeps the production path
# scorecard-aware without maintaining a second parser.
main.scorecard_url = _scorecard_url
main.team_code = _team_code
main.team_flag = _team_flag
main.extract_teams = _extract_teams
main.parse_scores = _parse_scores
main.parse_captains = _parse_captains


def txt(v):
    s=html.unescape(str(v or "")).replace("u003c","<").replace("u003e",">")
    return " ".join(re.sub(r"<[^>]+>"," ",s).split())


def label(r):
    e=str(r.get("event") or "").lower(); t=txt(r.get("commText") or r.get("commentary")).lower()
    x=r.get("ballLabel") or r.get("ball_label")
    if x not in (None,""): return str(x)
    if "wicket" in e:return "W"
    if "six" in e:return "6"
    if "four" in e:return "4"
    if "wide" in e:return "WD"
    if "noball" in e or "no_ball" in e or "no ball" in e:return "NB"
    if "legbye" in e or "leg bye" in e:return "LB"
    if re.search(r"\bbye\b",e):return "B"
    if re.search(r"\bno\s+runs?\b|\bno\s+run\b|\bdot\b",t):return "0"
    m=re.search(r"\b(\d+)\s+runs?\b",t)
    return m.group(1) if m else str(r.get("totalRuns",0))


def current_over(raw):
    rows=raw.get("matchCommentary") or raw.get("commentaryList") or []
    if isinstance(rows,dict): rows=rows.get("commentaryList") or rows.get("comments") or []
    items=[]
    for i,r in enumerate(rows if isinstance(rows,list) else []):
        if not isinstance(r,dict):continue
        try:m=float(r.get("ballMetric") or r.get("overNumber") or r.get("overNum"))
        except (TypeError,ValueError):continue
        if m<=0:continue
        try:ts=float(r.get("timestamp") or 0)
        except (TypeError,ValueError):ts=0
        items.append((ts,-i,m,str(r.get("inningsId",r.get("innings",""))),r))
    if not items:return dict(EMPTY)
    last=max(items); ov=int(last[2]); inn=last[3]; chosen={}
    for item in items:
        if int(item[2])!=ov or (inn and item[3] and item[3]!=inn):continue
        k=round(item[2],3)
        if k not in chosen or not txt(chosen[k][4].get("commText") or chosen[k][4].get("commentary")): chosen[k]=item
    balls=[{"label":label(x[4]),"ball":f"{x[2]:g}"} for x in sorted(chosen.values(),key=lambda x:x[2])]
    lt=txt(last[4].get("commText") or last[4].get("commentary")).lower()
    return {"over":str(ov),"balls":balls,"free_hit":"free hit" in lt or "freehit" in lt,"last_ball":balls[-1]["label"] if balls else ""}


def _completed_result(text, live_status=""):
    combined = " ".join(x for x in (str(text or ""), str(live_status or "")) if x)
    m = re.search(r"([A-Za-z][A-Za-z .&'-]{1,60}\s+won by\s+[^.]{1,100}?(?:runs?|wickets?|innings?|remaining))", combined, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    for phrase in ("Match tied", "Match abandoned due to rain", "No result", "Match drawn", "Match completed"):
        if phrase.lower() in combined.lower():
            return phrase
    return ""


def _scorecard_final(match, data):
    """Replace stale live data with the published final scorecard."""
    try:
        snap = wsgi._scorecard_snapshot(match)
    except Exception as exc:
        print("final scorecard read failed:", repr(exc))
        snap = None
    if not snap:
        return data

    text = snap.get("text", "")
    scores = snap.get("scores") or []
    if not scores:
        scores = _parse_scores(text)
    result = _completed_result(text, data.get("status", "") if isinstance(data, dict) else "")
    if not result or len(scores) < 2:
        return data

    t1, t2 = data.get("team1", "TEAM 1"), data.get("team2", "TEAM 2")
    matched = {}
    for s in scores:
        if wsgi._team_matches(s.get("code", ""), t1): matched["team1"] = s
        elif wsgi._team_matches(s.get("code", ""), t2): matched["team2"] = s

    if len(matched) < 2:
        ordered = scores[:2]
        if "team1" not in matched: matched["team1"] = ordered[0]
        if "team2" not in matched: matched["team2"] = ordered[1]

    for key in ("team1", "team2"):
        s = matched.get(key)
        if not s: continue
        data[key + "_score"] = f"{s['runs']}-{s['wickets']}"
        data[key + "_overs"] = str(s.get("overs", ""))

    data["current_over"] = dict(EMPTY)
    data["status"] = result
    data["final"] = True
    data["batting_team"] = ""
    data["crr"] = "-"
    data["partnership"] = "-"
    data["batsmen"] = []
    data["bowler"] = None

    mwin = re.match(r"(.+?)\s+won by\b", result, re.I)
    if mwin:
        winner = mwin.group(1).strip()
        winner_idx = None
        for i, team in enumerate((t1, t2)):
            if wsgi._team_matches(winner, team):
                winner_idx = i
                break
        if winner_idx is not None:
            data["batting_index"] = winner_idx
            data["bowling_index"] = 1 - winner_idx
    return data


def selected_score():
    mid=str(request.args.get("match_id","")).strip()
    if not mid.isdigit():return selector_entry._fixed_selected_score()
    try:match=next((m for m in selector_entry.get_matches() if str(m.get("id"))==mid),None)
    except Exception:match=None
    match=match or {"id":mid,"name":f"Match {mid}","url":f"https://www.cricbuzz.com/live-cricket-scores/{mid}"}
    try:
        # Scorecard-aware production parser first. This prevents a completed
        # match from staying frozen at its last live over.
        data=wsgi._extract_live(match)
        if data is None:
            data=main.live_detail(mid)
        data=_scorecard_final(match,data)
        if not data.get("captains"):
            try:
                payload=wsgi._live_data(match)
                data["captains"]=main.extract_captains(payload or {},data.get("team1",""),data.get("team2",""),mid)
            except Exception: pass
        data["url"]=match.get("url","")
        data["current_over"]=dict(data.get("current_over") or EMPTY)
    except Exception as exc:
        print("live parser/current-over error:",repr(exc))
        try:data=main.live_detail(mid)
        except Exception:data=None
    if data is None:
        data={"title":match.get("name","CRICKET LIVE"),"url":match.get("url",""),"team1":"LIVE DATA","team2":"RETRYING","team1_score":"-","team2_score":"-","team1_overs":"","team2_overs":"","crr":"-","partnership":"-","status":"LIVE DATA RETRYING","batsmen":[],"bowler":None,"captains":[],"batting_index":0,"bowling_index":1,"current_over":dict(EMPTY)}
    data.setdefault("current_over",dict(EMPTY));data.setdefault("batsmen",[]);data.setdefault("captains",[]);data.setdefault("crr","-");data.setdefault("partnership","-");data.setdefault("status","LIVE")
    data.setdefault("team1_score","-");data.setdefault("team2_score","-");data.setdefault("team1_overs","");data.setdefault("team2_overs","");data.setdefault("batting_index",0);data.setdefault("bowling_index",1-int(data.get("batting_index",0) or 0))
    resp=selector_entry.jsonify({"match":data,"error":None});resp.headers["Cache-Control"]="no-store, no-cache, max-age=0";return resp


def scoreboard():
    """Serve the OBS board with a small final-match presentation fix."""
    mid = str(request.args.get("match_id", "")).strip()
    if not mid.isdigit():
        return Response("Select a match first: <a href='/select-match'>Match Selector</a>", mimetype="text/html")
    try:
        html_doc = Path("static/scoreboard_full_v2.html").read_text(encoding="utf-8")
        enhancement = r'''<style>.final-side-score{color:#ffd21a;font-size:clamp(15px,1.7vw,32px);font-weight:1000;margin-top:.4vw}</style><script>(function(){let finalMatch=null;const originalFetch=window.fetch;window.fetch=async function(){const response=await originalFetch.apply(this,arguments);try{const u=String(arguments[0]||'');if(u.indexOf('/selected-score?')!==-1){response.clone().json().then(function(j){finalMatch=j&&j.match?j.match:null;setTimeout(decorateFinal,0)}).catch(function(){})}}catch(e){}return response};function decorateFinal(){if(!finalMatch||!finalMatch.final)return;const bi=Number(finalMatch.batting_index||0)===1?1:0;const rightScore=bi===1?finalMatch.team1_score:finalMatch.team2_score;const rightOvers=bi===1?finalMatch.team1_overs:finalMatch.team2_overs;const el=document.querySelector('.side.right .bowling');if(el){el.className='final-side-score';el.textContent=(rightScore||'-')+(rightOvers?' • '+rightOvers+' OVERS':'')}}setInterval(decorateFinal,500)})();</script>'''
        html_doc = html_doc.replace("</head>", enhancement + "</head>")
        return Response(html_doc, mimetype="text/html", headers={"Cache-Control":"no-store, no-cache, must-revalidate, max-age=0"})
    except Exception as exc:
        return Response("Scoreboard template error: " + str(exc), status=500, mimetype="text/plain")


ep=next((r.endpoint for r in app.url_map.iter_rules() if r.rule=="/selected-score"),None)
if ep:app.view_functions[ep]=selected_score
else:app.add_url_rule("/selected-score",endpoint="selected_score_fixed",view_func=selected_score,methods=["GET"])

for rule in list(app.url_map.iter_rules()):
    if rule.rule == "/scoreboard":
        app.view_functions[rule.endpoint] = scoreboard
        break
else:
    app.add_url_rule("/scoreboard",endpoint="scoreboard_fixed",view_func=scoreboard,methods=["GET"])
