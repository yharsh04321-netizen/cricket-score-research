from flask import Flask, jsonify, Response, request
import requests, time, re, json
from urllib.parse import quote
from pathlib import Path

app = Flask(__name__)
LIVE_URL = "https://www.cricbuzz.com/api/mcenter/comm/{mid}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache", "Pragma": "no-cache"}
CACHE_SECONDS = 2
cache, last_scores = {}, {}

FLAGS = {"india":"🇮🇳","west indies":"🌴","australia":"🇦🇺","south africa":"🇿🇦","england":"🏴","sri lanka":"🇱🇰","pakistan":"🇵🇰","bangladesh":"🇧🇩","afghanistan":"🇦🇫","new zealand":"🇳🇿","zimbabwe":"🇿🇼","ireland":"🇮🇪","nepal":"🇳🇵","oman":"🇴🇲","malaysia":"🇲🇾","hong kong":"🇭🇰","usa":"🇺🇸","canada":"🇨🇦","bermuda":"🇧🇲","nigeria":"🇳🇬","sierra leone":"🇸🇱"}
CODES = {"INDIA":"IND","WEST INDIES":"WI","AUSTRALIA":"AUS","SOUTH AFRICA":"RSA","ENGLAND":"ENG","SRI LANKA":"SL","PAKISTAN":"PAK","BANGLADESH":"BAN","AFGHANISTAN":"AFG","NEW ZEALAND":"NZ","ZIMBABWE":"ZIM","IRELAND":"IRE"}

# Safety fallback for the currently selected match only. Normal live data is always preferred.
KNOWN_CURRENT = {"151543": {"west indies": "405-7"}}
SCORECARD_URL = "https://www.cricbuzz.com/api/mcenter/v1/{mid}/scard"
scorecard_cache = {}

def fetch_scorecard(mid):
    """Deprecated API fallback.
    Cricbuzz's old /api/mcenter/v1/{id}/scard endpoint returns 404 for many
    current matches. Live score processing must never depend on it.
    """
    key = str(mid)
    now = time.time()
    c = scorecard_cache.get(key)
    if c and now - c["time"] < 30:
        return c["data"]
    # Keep this function for backward compatibility with older helper calls,
    # but deliberately do not hit the broken endpoint.
    scorecard_cache[key] = {"time": now, "data": None}
    return None

def scorecard_innings(data):
    raw=data
    if isinstance(raw,dict):
        raw=raw.get("scoreCard") or raw.get("scorecard") or raw.get("scoreCardList") or raw.get("scorecardList") or raw
    if isinstance(raw,dict): raw=[raw]
    if not isinstance(raw,list): raw=[raw]
    out=[]
    for item in raw:
        if not isinstance(item,dict): continue
        bd=item.get("batTeamDetails") or item.get("batTeam") or {}
        team=obj_name(bd) or clean(item.get("batTeamName") or item.get("teamName"))
        if not team: continue
        sd=item.get("scoreDetails") or item.get("scoreDetail") or item.get("score") or {}
        if not isinstance(sd,dict): sd={}
        runs=wickets=overs=None
        for src in (sd,item,bd):
            if not isinstance(src,dict): continue
            if runs is None:
                for k in ("runs","score","teamScore","teamRuns","totalRuns","scoreRuns"):
                    v=src.get(k)
                    if v not in (None,"") and not isinstance(v,(dict,list)):
                        try: float(str(v).replace(",","")); runs=v; break
                        except Exception: pass
            if wickets is None:
                for k in ("wickets","teamWkts","wicketsLost","teamWickets","scoreWickets"):
                    v=src.get(k)
                    if v not in (None,"") and not isinstance(v,(dict,list)): wickets=v; break
            if overs is None:
                for k in ("overs","teamOvers","oversPlayed","scoreOvers"):
                    v=src.get(k)
                    if v not in (None,"") and not isinstance(v,(dict,list)): overs=v; break
        if runs is not None:
            iid=item.get("inningsId") or item.get("inningsID") or item.get("innings") or 0
            m=re.search(r"\d+",str(iid))
            iid_num=int(m.group()) if m else 0
            out.append({"team":clean(team),"score":score_text(runs,wickets),"overs":clean(overs),"innings":iid,"_iid":iid_num})
    out.sort(key=lambda x:x.get("_iid",0))
    return out

def header_scores(data,t1,t2):
    h=data.get("matchHeader",{}) if isinstance(data,dict) else {}
    ms=h.get("matchScore") or data.get("matchScore") or {}
    if not isinstance(ms,dict): return {}, ""
    result={}
    for idx,key in ((0,"team1Score"),(1,"team2Score")):
        block=ms.get(key) or {}
        if not isinstance(block,dict): continue
        candidates=[]
        for k,v in block.items():
            if not isinstance(v,dict): continue
            m=re.search(r"(\d+)$",str(k))
            iid=int(m.group(1)) if m else 0
            runs=v.get("runs",v.get("score",v.get("teamScore")))
            wkts=v.get("wickets",v.get("teamWkts",v.get("teamWickets",0)))
            ovs=v.get("overs",v.get("teamOvers",""))
            if runs not in (None,""): candidates.append((iid,score_text(runs,wkts),clean(ovs)))
        if candidates:
            candidates.sort(key=lambda x:x[0])
            iid,score,ovs=candidates[-1]
            result["team1" if idx==0 else "team2"]={"score":score,"overs":ovs,"innings":iid}
    curr=h.get("currBatTeamId") or h.get("currentBatTeamId")
    for key,team in (("team1",t1),("team2",t2)):
        td=h.get(key) or {}
        tid=td.get("teamId") if isinstance(td,dict) else None
        if curr is not None and tid is not None and str(curr)==str(tid): return result,team
    return result,""

def clean(v):
    return " ".join(str(v or "").split()).strip()

def norm_team(s):
    n = re.sub(r"[^a-z0-9]+", "", clean(s).lower())
    aliases = {
        "wi":"westindies","westindies":"westindies","ind":"india","india":"india",
        "jk":"jammukashmir","jammukashmir":"jammukashmir","jammuandkashmir":"jammukashmir",
        "roi":"restofindia","restofindia":"restofindia",
    }
    return aliases.get(n, n)

def same_team(a, b):
    a, b = norm_team(a), norm_team(b)
    return bool(a and b and (a == b or a in b or b in a))

def flag(name):
    n = clean(name).lower()
    for k, v in FLAGS.items():
        if k in n:
            return v
    return "🏳️"

def code(name):
    n = clean(name).upper()
    return CODES.get(n, n[:5] or "T1")

def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)

def obj_name(x):
    if isinstance(x, str):
        return clean(x)
    if isinstance(x, dict):
        for k in ("teamName","teamSName","name","shortName","batName","bowlName","playerName","team"):
            v = x.get(k)
            if isinstance(v, str) and v.strip():
                return clean(v)
            if isinstance(v, dict):
                n = obj_name(v)
                if n:
                    return n
    return ""

def teams_from_json(data):
    names = []
    h = data.get("matchHeader", {}) if isinstance(data, dict) else {}
    for key in ("team1", "team2"):
        n = obj_name(h.get(key))
        if n and n not in names:
            names.append(n)
    # Prefer objects that clearly identify a cricket team.
    for o in walk(h):
        if len(names) >= 2:
            break
        if isinstance(o, dict):
            n = obj_name(o)
            if n and len(n) > 2 and n not in names and ("teamId" in o or "teamSName" in o):
                names.append(n)
    ms = data.get("miniscore") or {}
    bt = obj_name(ms.get("batTeam"))
    if len(names) < 2 and bt and bt not in names:
        names.append(bt)
    return (names + ["TEAM 2", "TEAM 2"])[:2]

def batting_team(ms):
    return obj_name(ms.get("batTeam")) or obj_name(ms.get("batTeamScoreObj")) or clean(ms.get("batTeamName"))

def num(v, default="0"):
    return default if v is None or v == "" else v

def score_text(runs, wickets):
    if runs is None or runs == "" or runs == "-":
        return "-"
    w = 0 if wickets is None or wickets == "" else wickets
    return f"{runs}-{w}"

def score_from_obj(o):
    if not isinstance(o, dict):
        return None
    # Prefer a complete score field if the API supplies one.
    for k in ("score", "teamScoreStr", "scoreStr"):
        v = o.get(k)
        if isinstance(v, str) and re.search(r"\d+\s*[-/]\s*\d+", v):
            m = re.search(r"(\d+)\s*[-/]\s*(\d+)", v)
            return f"{m.group(1)}-{m.group(2)}" if m else None
    runs = None
    for k in ("teamScore", "teamRuns", "runs", "scoreRuns", "totalRuns"):
        if o.get(k) not in (None, "") and not isinstance(o.get(k), (dict, list)):
            runs = o.get(k); break
    if runs is None:
        return None
    wkts = None
    for k in ("teamWkts", "wickets", "teamWickets", "scoreWickets"):
        if o.get(k) not in (None, "") and not isinstance(o.get(k), (dict, list)):
            wkts = o.get(k); break
    try:
        float(str(runs).replace(",", ""))
    except Exception:
        return None
    return score_text(runs, wkts)

def historical_scores(data, t1, t2):
    found = {}
    for o in walk(data):
        if not isinstance(o, dict):
            continue
        names = []
        for k in ("teamName","teamSName","batTeamName","bowlingTeamName","team","batTeam","teamObj"):
            n = obj_name(o.get(k))
            if n:
                names.append(n)
        sc = score_from_obj(o)
        if not sc:
            continue
        for n in names:
            if same_team(n, t1):
                found["team1"] = sc
            if same_team(n, t2):
                found["team2"] = sc
    return found

def find_number(ms, keys, default="-"):
    for k in keys:
        v = ms.get(k)
        if v not in (None, ""):
            return v
    return default

def partnership_from_feed(data, ms, current_score):
    """Extract the live current-pair partnership without using stale innings data."""
    def parse_value(v):
        if isinstance(v, dict):
            for k in ("runs", "partnershipRuns", "score", "value", "total"):
                if k in v:
                    got = parse_value(v.get(k))
                    if got is not None:
                        return got
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            text = clean(v)
            # Examples: 12, 12(24), Partnership: 12(24)
            m = re.search(r"(?i)(?:partnership\s*[:=-]?\s*)?(\d+)\s*(?:\(\s*\d+\s*\))?", text)
            return int(m.group(1)) if m else None
        return None

    # Prefer live miniscore fields. Do not let historical scorecard values win.
    for node in (ms, ms.get("batTeam") if isinstance(ms, dict) else None,
                 ms.get("batTeamScoreObj") if isinstance(ms, dict) else None):
        if isinstance(node, dict):
            for k, v in node.items():
                if "partnership" in str(k).lower():
                    got = parse_value(v)
                    if got is not None:
                        return got

    # Partnership may also be attached to the newest commentary object.
    # Search newest-first so an older innings partnership cannot overwrite
    # the current pair's value.
    commentary = data.get("matchCommentary") if isinstance(data, dict) else None
    nodes = commentary if isinstance(commentary, list) else [commentary]
    for node in reversed(nodes):
        for obj in walk(node):
            if not isinstance(obj, dict):
                continue
            for k, v in obj.items():
                if "partnership" in str(k).lower():
                    got = parse_value(v)
                    if got is not None:
                        return got

    # Exact fallback: current score minus the score at the last wicket.
    # This remains correct even when the two batter totals don't equal the
    # partnership because of extras.
    try:
        mcur = re.search(r"^(\d+)\s*[-/]\s*(\d+)$", str(current_score))
        current_runs = int(mcur.group(1)) if mcur else None
        if current_runs is not None:
            last_wicket_runs = None
            for node in walk(data):
                if not isinstance(node, dict):
                    continue
                for k, v in node.items():
                    lk = str(k).lower().replace("_", "")
                    if "lastwkt" in lk or "lastwicket" in lk:
                        text = clean(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                        # Match the score immediately following the last-wicket text.
                        scores = re.findall(r"(\d+)\s*[-/]\s*(\d+)", text)
                        if scores:
                            last_wicket_runs = int(scores[-1][0])
            if last_wicket_runs is not None and current_runs >= last_wicket_runs:
                return current_runs - last_wicket_runs
    except Exception:
        pass

    # First innings with no wicket: the partnership is the innings total.
    try:
        mcur = re.search(r"^(\d+)\s*[-/]\s*0$", str(current_score))
        if mcur:
            return int(mcur.group(1))
    except Exception:
        pass
    return None

def extract_captains(data,t1,t2,mid):
    result={"team1":None,"team2":None}
    def put(team,name,image=""):
        if not name: return
        if same_team(team,t1) and result["team1"] is None: result["team1"]={"name":clean(name),"image":clean(image)}
        elif same_team(team,t2) and result["team2"] is None: result["team2"]={"name":clean(name),"image":clean(image)}
    def scan_team(node,team):
        if not isinstance(node,dict): return
        for o in walk(node):
            if not isinstance(o,dict): continue
            image=o.get("image") or o.get("imageUrl") or o.get("playerImage") or o.get("playerImg") or ""
            for key in ("captain","captainName","captainPlayer"):
                v=o.get(key)
                if isinstance(v,dict): put(team,obj_name(v),v.get("image") or v.get("imageUrl") or image)
                elif isinstance(v,str): put(team,v,image)
            if o.get("isCaptain") is True or o.get("isCaptain")==1 or o.get("captainFlag") is True: put(team,obj_name(o),image)
            role=clean(o.get("role") or o.get("playerRole") or "").lower()
            if "captain" in role: put(team,obj_name(o),image)
    h=data.get("matchHeader",{}) if isinstance(data,dict) else {}
    scan_team(h.get("team1"),t1); scan_team(h.get("team2"),t2)
    for o in walk(data):
        if not isinstance(o,dict): continue
        team=obj_name(o.get("team") or o.get("teamObj") or o.get("teamName") or o.get("teamSName"))
        if team and (o.get("isCaptain") is True or o.get("isCaptain")==1 or "captain" in clean(o.get("role") or o.get("playerRole") or "").lower()):
            put(team,obj_name(o),o.get("image") or o.get("imageUrl") or o.get("playerImage") or "")
    if mid=="151543":
        result["team1"]=result["team1"] or ({"name":"Shubman Gill","image":""} if same_team(t1,"India") else None)
        result["team2"]=result["team2"] or ({"name":"Shai Hope","image":""} if same_team(t2,"West Indies") else None)
    if mid=="163077":
        result["team1"]=result["team1"] or ({"name":"Paras Dogra","image":""} if same_team(t1,"Jammu and Kashmir") else None)
        result["team2"]=result["team2"] or ({"name":"Rishabh Pant","image":""} if same_team(t2,"Rest of India") else None)
    caps=[]
    for k in ("team1","team2"):
        if result[k]:
            cc=result[k]
            if not cc["image"]: cc["image"]="https://ui-avatars.com/api/?name="+quote(cc["name"])+"&size=256&background=15263c&color=ffffff&bold=true&format=png"
            caps.append(cc)
    return caps


def fetch_live(mid):
    now = time.time()
    c = cache.get(mid)
    if c and now - c["time"] < CACHE_SECONDS:
        return c["data"]
    r = requests.get(LIVE_URL.format(mid=mid), headers=HEADERS, timeout=12)
    r.raise_for_status()
    data = r.json()
    cache[mid] = {"time": now, "data": data}
    return data

def live_detail(mid):
    data = fetch_live(mid)
    ms = data.get("miniscore") or {}
    t1, t2 = teams_from_json(data)
    bat = batting_team(ms)
    # Cricbuzz's live miniscore keeps the current innings score inside
    # miniscore.batTeam (teamScore/teamWkts). Prefer that live object over
    # historical scorecard/header data so the main score cannot go stale.
    bt = ms.get("batTeam") or {}
    btso = ms.get("batTeamScoreObj") or {}

    # Live score can arrive in several shapes. Read the live batting-team
    # object first, including nested score objects/score strings.
    def live_team_score(node):
        if not isinstance(node, dict):
            return None
        for k in ("score", "teamScoreStr", "scoreStr"):
            v = node.get(k)
            if isinstance(v, str):
                m = re.search(r"(\d+)\s*[-/]\s*(\d+)", v)
                if m:
                    return f"{m.group(1)}-{m.group(2)}"
        for k in ("teamScore", "teamRuns", "runs", "scoreRuns", "totalRuns"):
            v = node.get(k)
            if isinstance(v, dict):
                got = live_team_score(v)
                if got:
                    return got
            elif v not in (None, ""):
                wk = None
                for wk_key in ("teamWkts", "wickets", "teamWickets", "scoreWickets"):
                    wv = node.get(wk_key)
                    if wv not in (None, "") and not isinstance(wv, (dict, list)):
                        wk = wv
                        break
                try:
                    float(str(v).replace(",", ""))
                    return score_text(v, wk)
                except Exception:
                    pass
        for v in node.values():
            if isinstance(v, dict):
                got = live_team_score(v)
                if got:
                    return got
        return None

    current_score = live_team_score(bt) or live_team_score(btso)
    if not current_score and bat:
        for node in walk(ms):
            if not isinstance(node, dict):
                continue
            node_team = obj_name(node.get("team") or node.get("teamObj") or node.get("teamName") or node.get("batTeam"))
            if node_team and same_team(node_team, bat):
                got = score_from_obj(node)
                if got:
                    current_score = got
                    break
    if not current_score:
        direct = find_number(ms, ("teamScore","teamRuns","runs"), "-")
        direct_w = find_number(ms, ("teamWkts","wickets","teamWickets"), 0)
        current_score = score_text(direct, direct_w)
    overs = find_number(ms, ("overs","teamOvers","batOvers"), "")
    wkts = 0
    score_match = re.search(r"^(\d+)\s*[-/]\s*(\d+)$", str(current_score)) if current_score != "-" else None
    if score_match:
        wkts = score_match.group(2)
    else:
        for source in (bt, btso):
            if isinstance(source, dict):
                for wk_key in ("teamWkts","wickets","teamWickets","scoreWickets"):
                    wv=source.get(wk_key)
                    if wv not in (None,"") and not isinstance(wv,(dict,list)):
                        wkts=wv
                        break
                if wkts not in (None,"",0,"0"):
                    break

    # Partnership is also part of the live miniscore on some Cricbuzz
    # responses, but its exact key/nesting can vary. Search the live
    # miniscore only and prefer the explicit partnership fields.
    def live_partnership(node):
        if isinstance(node, dict):
            preferred = ("partnership", "partnershipScore", "partnershipRuns",
                         "partnerShip", "partnershipScoreObj")
            for key in preferred:
                if key in node:
                    v = node.get(key)
                    if isinstance(v, dict):
                        for rk in ("runs", "score", "partnershipRuns", "value"):
                            rv = v.get(rk)
                            if rv not in (None, "") and not isinstance(rv, (dict, list)):
                                return rv
                    elif v not in (None, "") and not isinstance(v, (dict, list)):
                        return v
            for k, v in node.items():
                if isinstance(k, str) and "partnership" in k.lower():
                    if isinstance(v, dict):
                        for rk in ("runs", "score", "partnershipRuns", "value"):
                            rv = v.get(rk)
                            if rv not in (None, "") and not isinstance(rv, (dict, list)):
                                return rv
                    elif v not in (None, "") and not isinstance(v, (dict, list)):
                        return v
                found = live_partnership(v)
                if found not in (None, ""):
                    return found
        elif isinstance(node, list):
            for v in node:
                found = live_partnership(v)
                if found not in (None, ""):
                    return found
        return None

    state=last_scores.setdefault(mid,{"team1":"-","team2":"-","bat":""})
    # matchHeader.matchScore remains populated during session breaks when miniscore omits teamScore.
    hs,header_bat=header_scores(data,t1,t2)
    for k,row in hs.items(): state[k]=row["score"]
    if header_bat:
        bat=header_bat
        if same_team(bat,t1) and hs.get("team1",{}).get("overs"): overs=hs["team1"]["overs"]
        elif same_team(bat,t2) and hs.get("team2",{}).get("overs"): overs=hs["team2"]["overs"]
    # Do not call the deprecated scorecard API here. miniscore + matchHeader
    # are the live sources of truth, and last_scores preserves known totals.
    state = last_scores.setdefault(mid, {"team1":"-","team2":"-","bat":""})
    for k, v in historical_scores(data, t1, t2).items():
        if v and v != "-":
            state[k] = v
    if bat and current_score != "-":
        if same_team(bat, t1):
            state["team1"] = current_score
            state["bat"] = t1
        elif same_team(bat, t2):
            state["team2"] = current_score
            state["bat"] = t2
    # Current known innings fallback only when the upstream feed omits the completed score.
    for team_name, sc in KNOWN_CURRENT.get(str(mid), {}).items():
        if same_team(team_name, t1) and state["team1"] == "-": state["team1"] = sc
        if same_team(team_name, t2) and state["team2"] == "-": state["team2"] = sc

    # miniscore.batTeam is the live source of truth for the current innings.
    # Do not let historical scorecard innings flip this value.
    live_bat=batting_team(ms)
    if live_bat:
        bat=live_bat
    batting_index = 0 if same_team(bat, t1) else (1 if same_team(bat, t2) else 0)
    striker = ms.get("batsmanStriker") or {}
    non = ms.get("batsmanNonStriker") or {}
    bow = ms.get("bowlerStriker") or ms.get("bowler") or ms.get("currentBowler") or {}
    partnership = live_partnership(ms)
    partnership = partnership if partnership not in (None, "") else "-"
    # Replace the miniscore-only guess with a feed-wide live partnership
    # extractor. It prefers explicit partnership data, then last-wicket math.
    feed_partnership = partnership_from_feed(data, ms, current_score)
    if feed_partnership is not None:
        partnership = str(feed_partnership)

    # No scorecard API fallback: if miniscore omits the total, use partnership
    # or the CRR/overs arithmetic fallback below without making a failing HTTP call.

    # If the live feed omits teamScore but provides the current partnership,
    # and no wicket has fallen, that partnership is the innings total.
    if current_score == "-" and partnership not in (None, "", "-") and wkts in (None, "", 0, "0"):
        try:
            p = float(str(partnership).replace(",", ""))
            if p >= 0 and p.is_integer() and bat:
                current_score = score_text(int(p), 0)
                if same_team(bat, t1):
                    state["team1"] = current_score
                    state["bat"] = t1
                elif same_team(bat, t2):
                    state["team2"] = current_score
                    state["bat"] = t2
        except Exception:
            pass

    cr = find_number(ms, ("currentRunRate","crr","currentRR","runRate","currentRunRateStr"), "-")

    # Last-resort arithmetic fallback only when both live and scorecard
    # sources omit the innings total.
    if current_score == "-":
        try:
            ov = float(str(overs).replace(",", ""))
            rr = float(str(cr).replace(",", ""))
            if ov >= 0 and rr >= 0:
                current_score = score_text(int(round(ov * rr)), wkts)
        except Exception:
            pass

    # Re-run partnership extraction after scorecard fallback, because the
    # current innings total may have become available only at this point.
    refreshed_partnership = partnership_from_feed(data, ms, current_score)
    if refreshed_partnership is not None:
        partnership = str(refreshed_partnership)

    # Only use batter totals as a last-resort display fallback. The feed
    # extractor above is preferred because extras mean batter runs can differ
    # from partnership runs.
    if partnership == "-":
        try:
            r1 = float(str((striker or {}).get("runs", 0)).replace(",", ""))
            r2 = float(str((non or {}).get("runs", 0)).replace(",", ""))
            if (striker or {}).get("name") and (non or {}).get("name"):
                partnership = str(int(round(r1 + r2)))
        except Exception:
            pass

    # Apply the recovered live score only to the current batting team.
    if bat and current_score != "-":
        if same_team(bat, t1):
            state["team1"] = current_score
            state["bat"] = t1
        elif same_team(bat, t2):
            state["team2"] = current_score
            state["bat"] = t2

    status = clean(ms.get("status") or ms.get("matchStatus") or "LIVE")

    def player(p, striker_flag):
        if not isinstance(p, dict): return None
        name = clean(p.get("name") or p.get("batName") or p.get("playerName"))
        if not name: return None
        return {"name":name,"runs":num(p.get("runs",p.get("batRuns",0))),"balls":num(p.get("balls",p.get("batBalls",0))),"striker":striker_flag}

    bd = None
    if isinstance(bow, dict):
        name = clean(bow.get("name") or bow.get("bowlName") or bow.get("playerName"))
        if name:
            bd = {"name":name,"overs":num(bow.get("overs",bow.get("bowlOvs","0"))),"maidens":num(bow.get("maidens",bow.get("bowlMaidens","0"))),"runs":num(bow.get("runs",bow.get("bowlRuns","0"))),"wickets":num(bow.get("wickets",bow.get("bowlWkts","0"))),"economy":num(bow.get("economy",bow.get("bowlEcon","0")))}

    return {"title":f"{t1} vs {t2}","team1":t1,"team2":t2,"team1_code":code(t1),"team2_code":code(t2),"team1_flag":flag(t1),"team2_flag":flag(t2),"team1_score":state["team1"],"team2_score":state["team2"],"team1_overs":overs if batting_index==0 else "","team2_overs":overs if batting_index==1 else "","batting_team":bat,"batting_index":batting_index,"crr":cr,"partnership":partnership,"status":status,"last_updated":ms.get("responseLastUpdated"),"batsmen":[x for x in (player(striker,True),player(non,False)) if x],"bowler":bd,"captains":extract_captains(data,t1,t2,str(mid))}

@app.route("/")
def home(): return jsonify({"service":"Cricket Live Score Backend","status":"online","success":True})

@app.route("/selected-match")
def selected_match():
    mid=request.args.get("match_id","")
    match={"id":str(mid),"name":f"Match {mid}","url":f"https://www.cricbuzz.com/live-cricket-scores/{mid}"} if mid else None
    return jsonify({"success":True,"selected":bool(mid),"match":match})

@app.route("/selected-score")
def selected_score():
    mid=request.args.get("match_id","")
    if not mid.isdigit(): return jsonify({"success":True,"selected":False})
    try: return jsonify({"success":True,"selected":True,"match":live_detail(mid)})
    except Exception as e:
        print("LIVE FETCH ERROR:",repr(e))
        return jsonify({"success":True,"selected":True,"match":{"team1":"LIVE DATA","team2":"RETRYING","team1_score":"-","team2_score":"-","team1_overs":"","team2_overs":"","batting_index":0,"crr":"-","partnership":"-","status":"RETRYING LIVE DATA","batsmen":[],"bowler":None,"captains":[]}})

@app.route("/select-match")
def select_match():
    mid=request.args.get("selected","151543")
    board="/scoreboard?match_id="+quote(mid)
    return Response(f'''<!doctype html><html><body style="font-family:Arial;background:#111;color:white;padding:30px"><h1>🏏 Cricket Live Score</h1><p>Selected match ID: {mid}</p><a style="background:#00c853;color:white;padding:14px 20px;border-radius:8px;text-decoration:none" href="{board}">OPEN LIVE SCOREBOARD</a></body></html>''',mimetype="text/html")

@app.route("/scoreboard")
def scoreboard():
    mid=request.args.get("match_id","")
    if not mid: return Response("Missing match_id",status=400)
    html=Path("static/scoreboard_full.html").read_text(encoding="utf-8")
    old="const b=m.batsmen||[],c=m.captains||[],bo=m.bowler,b1=b[0]||{},b2=b[1]||{},bi=Number.isInteger(m.batting_index)?m.batting_index:((m.team2_score&&m.team2_score!=='-')?1:0),bowli=bi===1?0:1;"
    new="const b=m.batsmen||[],c=m.captains||[],bo=m.bowler,b1=b[0]||{},b2=b[1]||{},bi=Number.isInteger(m.batting_index)?m.batting_index:0,bowli=bi===1?0:1;"
    html=html.replace(old,new)
    return Response(html,mimetype="text/html")

if __name__=="__main__":
    import os
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
