from __future__ import annotations

import csv
import os
import sys
from datetime import datetime

from nhl_api_scraper import get_today_schedule, get_team_stats
from powerplay_model import PowerPlayModel
from edge_checker import calculate_edge

EDGE_THRESHOLD = 0.02
DEFAULT_LINE = 0.5
KELLY_FRACTION = 0.25  # quarter-Kelly for conservative sizing

IO_DIR = os.path.join(os.path.dirname(__file__), "IO Files")
os.makedirs(IO_DIR, exist_ok=True)


def _parse_american_odds(raw: str) -> int | None:
    """Convert a raw odds string to an integer, or None if blank/invalid."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"  Invalid odds '{raw}', skipping.")
        return None


def _lookup_team(stats, abbrev: str):
    """Return the stats row for *abbrev*, raising a clear error if missing."""
    rows = stats[stats["teamAbbrev"] == abbrev]
    if rows.empty:
        raise KeyError(
            f"No stats found for team '{abbrev}'. "
            "The season parameter may be wrong, or the team did not play yet."
        )
    return rows.iloc[0]


def view_today_games() -> None:
    schedule = get_today_schedule()
    if schedule.empty:
        print("\nNo games scheduled for today.")
        return

    print("\nToday's NHL Games:\n")
    for _, game in schedule.iterrows():
        print(f"  {game['away']} @ {game['home']}")


def _export_to_csv(results: list[dict], date_str: str) -> None:
    """Write full projection data for all props, edge analysis for priced props,
    and a summary row to a timestamped CSV file."""
    filename = f"pp_report_{date_str}.csv"
    path = os.path.join(IO_DIR, filename)

    fieldnames = [
        "date", "game", "team", "side", "pp_opps", "exp_goals",
        "model_prob", "fair_odds", "book_odds", "implied_prob",
        "edge", "kelly_pct", "ev_flag",
    ]

    rated = [r for r in results if r["odds"] is not None]

    def _implied(odds: int) -> float:
        return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in sorted(results, key=lambda x: (x["game"], x["team"], x["side"])):
            has_odds = r["odds"] is not None
            ev_flag = ""
            if has_odds:
                ev_flag = "+EV" if r["edge"] > EDGE_THRESHOLD else ("MARGINAL" if r["edge"] > 0 else "AVOID")
            writer.writerow({
                "date":        date_str,
                "game":        r["game"],
                "team":        r["team"],
                "side":        r["side"],
                "pp_opps":     round(r["pp_opps"], 3),
                "exp_goals":   round(r["exp_goals"], 3),
                "model_prob":  round(r["model_prob"], 4),
                "fair_odds":   r["fair"],
                "book_odds":   r["odds"] if has_odds else "",
                "implied_prob": round(_implied(r["odds"]), 4) if has_odds else "",
                "edge":        round(r["edge"], 4) if has_odds else "",
                "kelly_pct":   round(r["kelly"], 2) if has_odds else "",
                "ev_flag":     ev_flag,
            })

        if rated:
            best  = max(rated, key=lambda x: x["edge"])
            worst = min(rated, key=lambda x: x["edge"])
            pos   = [r for r in rated if r["edge"] > EDGE_THRESHOLD]
            writer.writerow({})
            writer.writerow({
                "date":        date_str,
                "game":        "SUMMARY",
                "team":        f"{len(results)} projections / {len(rated)} priced / {len(pos)} +EV",
                "side":        "",
                "pp_opps":     "",
                "exp_goals":   "",
                "model_prob":  "",
                "fair_odds":   "",
                "book_odds":   "",
                "implied_prob": "",
                "edge":        round(sum(r["edge"] for r in rated) / len(rated), 4),
                "kelly_pct":   "",
                "ev_flag":     f"Best: {best['prop']} {best['edge']:+.3f} | Worst: {worst['prop']} {worst['edge']:+.3f}",
            })

    print(f"\n  Exported to: {path}")


def _kelly_pct(prob: float, odds: int) -> float:
    """Return fractional Kelly stake as a percentage of bankroll."""
    b = (odds / 100) if odds > 0 else (100 / abs(odds))
    kelly = (b * prob - (1 - prob)) / b
    return max(kelly * KELLY_FRACTION, 0.0)


def _group_by_game(results: list[dict]) -> dict[str, dict]:
    """Return results grouped as {game_key: {away, home, teams: {abbrev: {side: result}}}}."""
    games: dict[str, dict] = {}
    for r in results:
        gk = r["game"]
        if gk not in games:
            away, home = gk.split("@")
            games[gk] = {"away": away, "home": home, "teams": {}}
        td = games[gk]["teams"]
        if r["team"] not in td:
            td[r["team"]] = {}
        td[r["team"]][r["side"]] = r
    return games


def _print_session_report(results: list[dict]) -> None:
    if not results:
        print("\n  No data to report.")
        return

    rated   = [r for r in results if r["odds"] is not None]
    pos_ev  = sorted([r for r in rated if r["edge"] > EDGE_THRESHOLD],
                     key=lambda r: r["edge"], reverse=True)
    neg_ev  = sorted([r for r in rated if r["edge"] <= 0],
                     key=lambda r: r["edge"])
    neutral = [r for r in rated if 0 < r["edge"] <= EDGE_THRESHOLD]

    over_key  = f"o{DEFAULT_LINE}"
    under_key = f"U{DEFAULT_LINE}"
    w   = 88
    div = "=" * w

    print(f"\n\n{div}")
    print(f"  DAILY REPORT  —  {datetime.today().strftime('%A %b %d, %Y')}")
    print(div)

    # ── Section 1: Game-by-game projections ─────────────────────────────────
    print(f"\n  GAME PROJECTIONS\n")
    col = f"  {'TEAM':<6}  {'PP OPPS':>7}  {'EXP G':>5}  {'o0.5':>5}  {'U0.5':>5}"
    col_line = f"  {'─'*6}  {'─'*7}  {'─'*5}  {'─'*5}  {'─'*5}"
    edge_hdr  = f"  {'SIDE':<6}  {'BOOK':>6}  {'MODEL':>6}  {'FAIR':>6}  {'EDGE':>7}  {'KELLY':>6}  FLAG"
    edge_line = f"  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*6}  {'─'*4}"

    games = _group_by_game(results)
    for gk, gdata in games.items():
        away, home = gdata["away"], gdata["home"]
        print(f"  {away} @ {home}")
        print(col)
        print(col_line)

        for team in (home, away):
            sides = gdata["teams"].get(team, {})
            ov = sides.get(over_key)
            un = sides.get(under_key)
            if ov is None:
                continue
            print(
                f"  {team:<6}  {ov['pp_opps']:>7.2f}  {ov['exp_goals']:>5.2f}"
                f"  {ov['model_prob']:>5.3f}  {un['model_prob']:>5.3f}"
                if un else
                f"  {team:<6}  {ov['pp_opps']:>7.2f}  {ov['exp_goals']:>5.2f}"
                f"  {ov['model_prob']:>5.3f}  {'—':>5}"
            )

        # Show entered lines for this game, if any
        game_rated = [r for r in rated if r["game"] == gk]
        if game_rated:
            print()
            print(edge_hdr)
            print(edge_line)
            for r in sorted(game_rated, key=lambda x: x["edge"], reverse=True):
                flag = "+EV" if r["edge"] > EDGE_THRESHOLD else ("MRGN" if r["edge"] > 0 else "AVOID")
                print(
                    f"  {r['prop']:<6}  {r['odds']:>+6d}  {r['model_prob']:>6.3f}"
                    f"  {r['fair']:>+6d}  {r['edge']:>+7.3f}  {r['kelly']:>5.1f}%  {flag}"
                )
        print()

    # ── Section 2: Full edge rankings ───────────────────────────────────────
    if rated:
        print(f"{'─' * w}")
        print(f"  EDGE RANKINGS  ({len(rated)} props priced)\n")
        print(f"  {'PROP':<22} {'GAME':<12} {'MODEL':>6} {'FAIR':>6} {'BOOK':>6} {'EDGE':>7} {'KELLY':>7}  FLAG")
        print(f"  {'─'*22} {'─'*12} {'─'*6} {'─'*6} {'─'*6} {'─'*7} {'─'*7}  {'─'*5}")
        for r in sorted(rated, key=lambda r: r["edge"], reverse=True):
            flag = "+EV " if r["edge"] > EDGE_THRESHOLD else ("MRGN" if r["edge"] > 0 else "AVOD")
            print(
                f"  {r['prop']:<22} {r['game']:<12} "
                f"{r['model_prob']:>6.3f} {r['fair']:>+6d} {r['odds']:>+6d} "
                f"{r['edge']:>+7.3f} {r['kelly']:>6.1f}%  {flag}"
            )

    # ── Section 3: +EV callouts ─────────────────────────────────────────────
    print(f"\n{'─' * w}")
    print(f"  +EV BETS  ({len(pos_ev)} found, threshold > {EDGE_THRESHOLD:.0%})")
    print(f"{'─' * w}")
    if pos_ev:
        for r in pos_ev:
            print(
                f"  *** {r['prop']:<20} {r['game']:<12}"
                f"  Edge: {r['edge']:+.3f}   Model: {r['model_prob']:.3f}"
                f"   Book: {r['odds']:+d}   Kelly: {r['kelly']:.1f}%"
            )
    else:
        print("  None.")

    if neutral:
        print(f"\n{'─' * w}")
        print(f"  MARGINAL  (0 < edge <= {EDGE_THRESHOLD:.0%})")
        print(f"{'─' * w}")
        for r in neutral:
            print(f"  {r['prop']:<20} {r['game']:<12}  Edge: {r['edge']:+.3f}   Book: {r['odds']:+d}")

    if neg_ev:
        print(f"\n{'─' * w}")
        print("  AVOID  (negative edge)")
        print(f"{'─' * w}")
        for r in neg_ev[:5]:
            print(f"  {r['prop']:<20} {r['game']:<12}  Edge: {r['edge']:+.3f}   Book: {r['odds']:+d}")

    # ── Section 4: Summary ───────────────────────────────────────────────────
    print(f"\n{'─' * w}")
    print("  SUMMARY")
    print(f"{'─' * w}")
    print(f"  Games today      : {len(games)}")
    print(f"  Props priced     : {len(rated)}")
    print(f"  +EV bets         : {len(pos_ev)}")
    print(f"  Marginal         : {len(neutral)}")
    print(f"  Negative edge    : {len(neg_ev)}")
    if rated:
        best     = max(rated, key=lambda r: r["edge"])
        worst    = min(rated, key=lambda r: r["edge"])
        avg_edge = sum(r["edge"] for r in rated) / len(rated)
        print(f"  Best edge        : {best['edge']:+.3f}  ({best['prop']} — {best['game']})")
        print(f"  Worst edge       : {worst['edge']:+.3f}  ({worst['prop']} — {worst['game']})")
        print(f"  Avg edge         : {avg_edge:+.3f}")
    print(div)

    choice = input("\n  Export report to CSV? (y/n): ").strip().lower()
    if choice == "y":
        _export_to_csv(results, datetime.today().strftime("%Y-%m-%d"))


def enter_lines_for_today() -> None:
    schedule = get_today_schedule()
    if schedule.empty:
        print("\nNo games scheduled for today.")
        return

    stats = get_team_stats()
    league_avg_pk_fail = (1 - stats["penaltyKillPct"]).mean()
    results: list[dict] = []

    print("\n=== ENTER LINES FOR TODAY'S GAMES ===\n")

    for _, game in schedule.iterrows():
        home: str = game["home"]
        away: str = game["away"]

        try:
            home_row = _lookup_team(stats, home)
            away_row = _lookup_team(stats, away)
        except KeyError as exc:
            print(f"  Skipping {away} @ {home}: {exc}")
            continue

        home_model = PowerPlayModel(home_row, away_row, league_avg_pk_fail=league_avg_pk_fail)
        away_model = PowerPlayModel(away_row, home_row, league_avg_pk_fail=league_avg_pk_fail)

        print(f"\n{away} @ {home}")
        print("-" * 30)

        for team, model in ((home, home_model), (away, away_model)):
            over_raw  = input(f"  {team} Over  {DEFAULT_LINE} PP Goals line (press Enter to skip): ")
            under_raw = input(f"  {team} Under {DEFAULT_LINE} PP Goals line (press Enter to skip): ")

            over_prob  = model.probability_over(DEFAULT_LINE)
            under_prob = 1 - over_prob

            print(f"  {team}  Exp goals: {model.expected_goals():.2f}  "
                  f"PP opps: {model.project_opportunities():.2f}")

            for side, prob, raw in (
                (f"o{DEFAULT_LINE}", over_prob,  over_raw),
                (f"U{DEFAULT_LINE}", under_prob, under_raw),
            ):
                odds  = _parse_american_odds(raw)
                fair  = model.fair_odds(prob)
                edge  = calculate_edge(prob, odds) if odds is not None else None
                kelly = _kelly_pct(prob, odds) * 100 if odds is not None else 0.0

                marker = "  *** +EV ***" if (edge is not None and edge > EDGE_THRESHOLD) else ""
                print(
                    f"    {side:<6}  Model: {prob:.3f}  Fair: {fair:+d}"
                    + (f"  Book: {odds:+d}  Edge: {edge:+.3f}  Kelly: {kelly:.1f}%{marker}"
                       if odds is not None else "")
                )

                results.append({
                    "game":      f"{away}@{home}",
                    "team":      team,
                    "side":      side,
                    "prop":      f"{team} {side}",
                    "pp_opps":   model.project_opportunities(),
                    "exp_goals": model.expected_goals(),
                    "model_prob": prob,
                    "fair":      fair,
                    "odds":      odds,
                    "edge":      edge,
                    "kelly":     kelly,
                })

        print("-" * 40)

    _print_session_report(results)


def full_projection_scan() -> None:
    schedule = get_today_schedule()
    if schedule.empty:
        print("\nNo games scheduled for today.")
        return

    stats = get_team_stats()
    league_avg_pk_fail = (1 - stats["penaltyKillPct"]).mean()

    print("\n=== FULL DAILY PROJECTION SCAN ===\n")

    for _, game in schedule.iterrows():
        home: str = game["home"]
        away: str = game["away"]

        try:
            home_row = _lookup_team(stats, home)
            away_row = _lookup_team(stats, away)
        except KeyError as exc:
            print(f"  Skipping {away} @ {home}: {exc}")
            continue

        home_model = PowerPlayModel(home_row, away_row, league_avg_pk_fail=league_avg_pk_fail)
        away_model = PowerPlayModel(away_row, home_row, league_avg_pk_fail=league_avg_pk_fail)

        print(f"{away} @ {home}")
        for label, model in (("Home", home_model), ("Away", away_model)):
            over_prob  = model.probability_over(DEFAULT_LINE)
            under_prob = 1 - over_prob
            print(f"  {label}  PP opps: {model.project_opportunities():.2f}  "
                  f"Exp goals: {model.expected_goals():.2f}  "
                  f"o{DEFAULT_LINE}: {over_prob:.3f}  "
                  f"U{DEFAULT_LINE}: {under_prob:.3f}")
        print("-" * 40)


def _parse_lines_file(path: str) -> dict[tuple[str, str, str], int | None]:
    """Parse a betting-lines .txt file and return a mapping of
    (game_key, team_abbrev, side) -> American odds (or None if blank).

    File format (one team per line, comments with #):
        AWAY@HOME,TEAM,o0.5_odds,U0.5_odds

    Example:
        TOR@BOS,TOR,-130,+110
        TOR@BOS,BOS,-150,+125
        # leave an odds field blank or use - to skip that side
        MTL@NYR,MTL,+105,
        MTL@NYR,NYR,-160,-135
    """
    lines_map: dict[tuple[str, str, str], int | None] = {}
    over_key  = f"o{DEFAULT_LINE}"
    under_key = f"U{DEFAULT_LINE}"

    with open(path, "r") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 4:
                print(f"  Line {lineno}: expected 4 columns, got {len(parts)} — skipping.")
                continue
            game, team, over_raw, under_raw = parts
            game = game.upper()
            team = team.upper()
            for side, raw_odds in ((over_key, over_raw), (under_key, under_raw)):
                if raw_odds in ("", "-"):
                    odds = None
                else:
                    odds = _parse_american_odds(raw_odds)
                lines_map[(game, team, side)] = odds

    return lines_map


def generate_lines_template() -> None:
    """Write a blank lines template for today's games to a .txt file."""
    schedule = get_today_schedule()
    if schedule.empty:
        print("\nNo games scheduled for today.")
        return

    date_str  = datetime.today().strftime("%Y-%m-%d")
    filename  = f"lines_{date_str}.txt"
    filepath  = os.path.join(IO_DIR, filename)

    with open(filepath, "w") as f:
        f.write(f"# NHL Power Play Lines — {date_str}\n")
        f.write(f"# Format: AWAY@HOME,TEAM,o{DEFAULT_LINE}_odds,U{DEFAULT_LINE}_odds\n")
        f.write("# Leave an odds field blank or use - to skip that side\n")
        f.write("#\n")
        for _, game in schedule.iterrows():
            away: str = game["away"]
            home: str = game["home"]
            game_key  = f"{away}@{home}"
            f.write(f"\n# {away} @ {home}\n")
            f.write(f"{game_key},{away},,\n")
            f.write(f"{game_key},{home},,\n")

    print(f"\n  Template written to: {filepath}")
    print("  Fill in the odds columns and use 'Load Lines From File' to run the model.")


def load_lines_from_file() -> None:
    """Prompt for a lines .txt file, parse it, run the model, and print the report."""
    date_str = datetime.today().strftime("%Y-%m-%d")
    default  = os.path.join(IO_DIR, f"lines_{date_str}.txt")

    prompt = f"\n  Path to lines file [{default}]: "
    raw_path = input(prompt).strip()
    filepath  = raw_path if raw_path else default

    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        return

    try:
        lines_map = _parse_lines_file(filepath)
    except OSError as exc:
        print(f"  Could not read file: {exc}")
        return

    if not lines_map:
        print("  No valid entries found in the file.")
        return

    schedule = get_today_schedule()
    if schedule.empty:
        print("\nNo games scheduled for today.")
        return

    stats = get_team_stats()
    league_avg_pk_fail = (1 - stats["penaltyKillPct"]).mean()
    results: list[dict] = []

    over_key  = f"o{DEFAULT_LINE}"
    under_key = f"U{DEFAULT_LINE}"

    print("\n=== LINES LOADED FROM FILE ===\n")

    for _, game in schedule.iterrows():
        home: str = game["home"]
        away: str = game["away"]
        game_key  = f"{away}@{home}"

        game_entries = {k: v for k, v in lines_map.items() if k[0] == game_key}
        if not game_entries:
            continue

        try:
            home_row = _lookup_team(stats, home)
            away_row = _lookup_team(stats, away)
        except KeyError as exc:
            print(f"  Skipping {game_key}: {exc}")
            continue

        home_model = PowerPlayModel(home_row, away_row, league_avg_pk_fail=league_avg_pk_fail)
        away_model = PowerPlayModel(away_row, home_row, league_avg_pk_fail=league_avg_pk_fail)

        print(f"{away} @ {home}")
        print("-" * 30)

        for team, model in ((home, home_model), (away, away_model)):
            over_prob  = model.probability_over(DEFAULT_LINE)
            under_prob = 1 - over_prob

            print(f"  {team}  Exp goals: {model.expected_goals():.2f}  "
                  f"PP opps: {model.project_opportunities():.2f}")

            for side, prob in ((over_key, over_prob), (under_key, under_prob)):
                odds_key = (game_key, team, side)
                if odds_key not in lines_map:
                    odds = None
                else:
                    odds = lines_map[odds_key]

                fair  = model.fair_odds(prob)
                edge  = calculate_edge(prob, odds) if odds is not None else None
                kelly = _kelly_pct(prob, odds) * 100 if odds is not None else 0.0

                marker = "  *** +EV ***" if (edge is not None and edge > EDGE_THRESHOLD) else ""
                print(
                    f"    {side:<6}  Model: {prob:.3f}  Fair: {fair:+d}"
                    + (f"  Book: {odds:+d}  Edge: {edge:+.3f}  Kelly: {kelly:.1f}%{marker}"
                       if odds is not None else "")
                )

                results.append({
                    "game":       game_key,
                    "team":       team,
                    "side":       side,
                    "prop":       f"{team} {side}",
                    "pp_opps":    model.project_opportunities(),
                    "exp_goals":  model.expected_goals(),
                    "model_prob": prob,
                    "fair":       fair,
                    "odds":       odds,
                    "edge":       edge,
                    "kelly":      kelly,
                })

        print("-" * 40)

    _print_session_report(results)


def main_menu() -> None:
    menu = {
        "1": ("View Today's Games",          view_today_games),
        "2": ("Enter Lines For Today's Games", enter_lines_for_today),
        "3": ("Load Lines From File",          load_lines_from_file),
        "4": ("Generate Lines Template",       generate_lines_template),
        "5": ("Full Projection Scan",          full_projection_scan),
        "6": ("Exit", None),
    }

    while True:
        print("\n====== NHL POWERPLAY MODEL ======")
        for key, (label, _) in menu.items():
            print(f"{key}. {label}")

        choice = input("\nSelect option: ").strip()

        if choice not in menu:
            print("Invalid selection.")
            continue

        label, fn = menu[choice]
        if fn is None:
            print("Exiting.")
            sys.exit(0)

        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"\nError: {exc}")


if __name__ == "__main__":
    main_menu()
