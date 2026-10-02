import re
import time
import requests
import main

LIVE_URL = "https://www.cricbuzz.com/api/mcenter/comm/{}"
HEADERS = dict(main.HEADERS)
HEADERS["User-Agent"] = "Mozilla/5.0 cricket-live-overlay/1.0"
CAPTAIN_CACHE = {}
ORIGINAL_FETCH = main.fetch_match_detail


def clean_name(value):
    return " ".join(str(value or "").split()).strip()


def norm(value):
    return re.sub(r"[^a-z0-9]", "", clean_name(value).lower())


def team_matches(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    aa = a.replace("indiaa", "inda").replace("australiaa", "ausa")
    bb = b.replace("indiaa", "inda").replace("australiaa", "ausa")
    return aa == bb


def team_name_from_obj(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("teamName", "teamFullName", "name", "team", "shortName", "teamShortName"):
        if obj.get(key):
            return clean_name(obj.get(key))
    return ""


def player_dict(obj, striker=False):
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("batName") or obj.get("batsmanName")
    if not name:
        return None
    runs = obj.get("runs", obj.get("batRuns", obj.get("r", 0)))
    balls = obj.get("balls", obj.get("batBalls", obj.get("b", 0)))
    return {"name": clean_name(name), "runs": str(runs), "balls": str(balls), "striker": bool(striker)}


def bowler_dict(obj):
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("bowlName") or obj.get("bowlerName")
    if not name:
        return None
    overs = obj.get("overs", obj.get("bowlOvs", obj.get("o", "")))
    maidens = obj.get("maidens", obj.get("bowlMaidens", obj.get("m", 0)))
    runs = obj.get("runs", obj.get("bowlRuns", obj.get("r", 0)))
    wickets = obj.get("wickets", obj.get("bowlWkts", obj.get("w", 0)))
    economy = obj.get("economy", obj.get("bowlEcon", obj.get("eco", "")))
    return {"name": clean_name(name), "overs": str(overs), "maidens": str(maidens), "runs": str(runs), "wickets": str(wickets), "economy": str(economy)}


def captain_data(team1, team2, match_url):
    key = f"{norm(team1)}|{norm(team2)}"
    cached = CAPTAIN_CACHE.get(key)
    if cached and time.time() - cached["time"] < 900:
        return cached["data"]

    caps = []
    try:
        r = requests.get(main.scorecard_url(match_url), headers=HEADERS, timeout=12)
        r.raise_for_status()
        soup_text = main.clean(main.BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
        caps = main.parse_captains(r.text, soup_text)
    except Exception:
        caps = []

    if "india" in norm(team1) and "australia" in norm(team2):
        desired = ["Devdutt Padikkal", "Peter Handscomb"]
    elif "australia" in norm(team1) and "india" in norm(team2):
        desired = ["Peter Handscomb", "Devdutt Padikkal"]
    else:
        desired = []

    result = []
    for wanted in desired:
        found = next((c for c in caps if norm(c.get("name")) == norm(wanted)), None)
        if not found:
            found = {"name": wanted, "image": "https://ui-avatars.com/api/?name=" + requests.utils.quote(wanted) + "&size=256&background=15263c&color=ffffff&bold=true&format=png"}
        result.append(found)

    if not result:
        result = caps[:2]
    CAPTAIN_CACHE[key] = {"time": time.time(), "data": result}
    return result


def fixed_fetch_match_detail(match):
    match_id = str(match["id"])
    team1, team2 = main.extract_teams(match.get("name", ""))
    result = {
        "title": match.get("name", "CRICKET"), "url": match.get("url", ""),
        "team1": team1, "team2": team2,
        "team1_code": main.team_code(team1), "team2_code": main.team_code(team2),
        "team1_flag": main.team_flag(team1), "team2_flag": main.team_flag(team2),
        "team1_score": "-", "team2_score": "-", "team1_overs": "", "team2_overs": "",
        "crr": "-", "partnership": "-", "status": "LIVE DATA TEMPORARILY UNAVAILABLE",
        "batsmen": [], "bowler": None, "captains": []
    }
    try:
        r = requests.get(LIVE_URL.format(match_id), headers=HEADERS, timeout=12)
        r.raise_for_status()
        data = r.json()
        mini = data.get("miniscore") or {}
        header = data.get("matchHeader") or {}

        header_teams = []
        for key in ("team1", "team2"):
            obj = header.get(key)
            if isinstance(obj, dict):
                n = team_name_from_obj(obj)
                if n:
                    header_teams.append(n)
        if len(header_teams) >= 2:
            team1, team2 = header_teams[0], header_teams[1]
            result.update({"team1": team1, "team2": team2, "team1_code": main.team_code(team1), "team2_code": main.team_code(team2), "team1_flag": main.team_flag(team1), "team2_flag": main.team_flag(team2), "title": f"{team1} vs {team2}"})

        bat_team_obj = mini.get("batTeam") or {}
        batting_team = team_name_from_obj(mini.get("batTeamScoreObj")) or team_name_from_obj(bat_team_obj)
        if not batting_team:
            batting_team = mini.get("batTeamName") or mini.get("batTeamShortName") or ""

        score = bat_team_obj.get("teamScore", bat_team_obj.get("score", mini.get("teamScore")))
        wickets = bat_team_obj.get("teamWkts", bat_team_obj.get("wickets", mini.get("teamWkts")))
        overs = mini.get("overs", mini.get("oversStr", ""))
        if score is not None and wickets is not None:
            score_text = f"{score}-{wickets}"
            if team_matches(batting_team, team1):
                result["team1_score"], result["team1_overs"] = score_text, str(overs)
            elif team_matches(batting_team, team2):
                result["team2_score"], result["team2_overs"] = score_text, str(overs)
            else:
                if norm(batting_team) == norm(result["team1_code"]):
                    result["team1_score"], result["team1_overs"] = score_text, str(overs)
                else:
                    result["team2_score"], result["team2_overs"] = score_text, str(overs)

        crr = mini.get("currentRunRate", mini.get("crr"))
        if crr is not None:
            result["crr"] = str(crr)

        partnership = mini.get("partnership") or mini.get("partnerShip") or mini.get("partnershipObj") or {}
        if isinstance(partnership, dict):
            pr = partnership.get("runs", partnership.get("partnershipRuns", partnership.get("r")))
            pb = partnership.get("balls", partnership.get("partnershipBalls", partnership.get("b")))
            if pr is not None:
                result["partnership"] = str(pr) + (f" ({pb})" if pb is not None else "")
        elif partnership not in (None, ""):
            result["partnership"] = str(partnership)

        result["status"] = main.clean(mini.get("status") or header.get("status") or "LIVE")
        result["batsmen"] = [p for p in (player_dict(mini.get("batsmanStriker"), True), player_dict(mini.get("batsmanNonStriker"), False)) if p]
        result["bowler"] = bowler_dict(mini.get("bowlerStriker") or mini.get("bowler") or mini.get("currentBowler"))
        result["captains"] = captain_data(team1, team2, match.get("url", ""))
        return result
    except Exception as e:
        print("livefix error:", repr(e))
        try:
            return ORIGINAL_FETCH(match)
        except Exception:
            return result


main.fetch_match_detail = fixed_fetch_match_detail
app = main.app
_original_scoreboard = app.view_functions.get("scoreboard")


def patched_scoreboard():
    response = _original_scoreboard()
    try:
        html = response.get_data(as_text=True)
        # The scoreboard JS has changed between versions. Patch any existing
        # update timer instead of relying on one exact spacing/interval string.
        html = re.sub(r"setInterval\(\s*update\s*,\s*\d+\s*\)", "setInterval(update,5000)", html)
        # Make every browser request bypass an intermediate HTTP cache.
        html = html.replace("fetch('/selected-score?match_id=' + MID)", "fetch('/selected-score?match_id=' + MID + '&_=' + Date.now(), {cache:'no-store'})")
        html = html.replace('fetch(`/selected-score?match_id=${MID}`)', 'fetch(`/selected-score?match_id=${MID}&_=${Date.now()}`, {cache:"no-store"})')
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.set_data(html)
    except Exception as e:
        print("scoreboard patch error:", repr(e))
    return response


if _original_scoreboard:
    app.view_functions["scoreboard"] = patched_scoreboard
