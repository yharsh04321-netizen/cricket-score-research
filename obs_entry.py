# Stable Render entrypoint for the Cricket Match Selector + OBS scoreboard.
from flask import request, redirect, jsonify
import selector_entry
import main

app = selector_entry.app


def _selected_score():
    # selector_entry already contains the production live-score pipeline:
    # fresh Cricbuzz mcenter data -> wsgi._extract_live -> score/header/status
    # fallbacks -> OBS score normalization. Do not route production traffic
    # back through main.live_detail(), which is the legacy parser.
    return selector_entry._fixed_selected_score()


# entry.py already owns the Flask app, so defining another @app.route('/') does
# not replace its existing health endpoint. Explicitly replace the registered
# view function instead.
def _home():
    return redirect("/select-match")

for rule in list(app.url_map.iter_rules()):
    if rule.rule == "/":
        app.view_functions[rule.endpoint] = _home
    elif rule.rule == "/selected-score":
        app.view_functions[rule.endpoint] = _selected_score

# Ensure these routes exist even if an older entry.py version is deployed.
if not any(r.rule == "/selected-score" for r in app.url_map.iter_rules()):
    app.add_url_rule("/selected-score", endpoint="obs_selected_score", view_func=_selected_score, methods=["GET"])
if not any(r.rule == "/cricket-selector" for r in app.url_map.iter_rules()):
    app.add_url_rule("/cricket-selector", endpoint="obs_selector", view_func=lambda: redirect("/select-match"), methods=["GET"])

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
