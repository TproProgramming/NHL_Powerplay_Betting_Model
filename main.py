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
    """Write all priced props plus a summary row to a timestamped CSV file."""
    filename = f"pp_report_{date_str}.csv"
    path = os.path.join(os.path.dirname(__file__), filename)

    fieldnames = [
        "date", "game", "prop", "model_prob", "fair_odds",
        "book_odds", "implied_prob", "edge", "kelly_pct", "ev_flag",
    ]

    rated = [r for r in results if r["odds"] is not None]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in sorted(rated, key=lambda x: x["edge"], reverse=True):
            implied = abs(r["odds"]) / (abs(r["odds"]) + 100) if r["odds"] < 0 else 100 / (r["odds"] + 100)
            ev_flag = "+EV" if r["edge"] > EDGE_THRESHOLD else ("MARGINAL" if r["edge"] > 0 else "AVOID")
            writer.writerow({
                "date":        date_str,
                "game":        r["game"],
                "prop":        r["prop"],
                "model_prob":  round(r["model_prob"], 4),
                "fair_odds":   r["fair"],
                "book_odds":   r["odds"],
                "implied_prob": round(implied, 4),
                "edge":        round(r["edge"], 4),
                "kelly_pct":   round(r["kelly"], 2),
                "ev_flag":     ev_flag,
            })

        # Summary row
        if rated:
            best  = max(rated, key=lambda x: x["edge"])
            worst = min(rated, key=lambda x: x["edge"])
            pos   = [r for r in rated if r["edge"] > EDGE_THRESHOLD]
            writer.writerow({})
            writer.writerow({
                "date":        date_str,
                "game":        "SUMMARY",
                "prop":        f"{len(rated)} props / {len(pos)} +EV",
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


def _print_session_report(results: list[dict]) -> None:
    if not results:
        print("\n  No lines were entered.")
        return

    rated   = [r for r in results if r["odds"] is not None]
    pos_ev  = sorted([r for r in rated if r["edge"] > EDGE_THRESHOLD],
                     key=lambda r: r["edge"], reverse=True)
    neg_ev  = sorted([r for r in rated if r["edge"] <= 0],
                     key=lambda r: r["edge"])
    neutral = [r for r in rated if 0 < r["edge"] <= EDGE_THRESHOLD]

    w = 74
    div = "=" * w

    print(f"\n\n{div}")
    print(f"  DAILY BETTING REPORT  —  {datetime.today().strftime('%A %b %d, %Y')}")
    print(div)

    # ── Full ranked table ───────────────────────────────────────────────────
    print(f"\n{'PROP':<22} {'GAME':<12} {'MODEL':>6} {'FAIR':>6} {'BOOK':>6} {'EDGE':>7} {'KELLY':>7}")
    print("-" * w)
    for r in sorted(rated, key=lambda r: r["edge"], reverse=True):
        ev_tag = " +" if r["edge"] > EDGE_THRESHOLD else ("  " if r["edge"] > 0 else " -")
        print(
            f"{r['prop']:<22} {r['game']:<12} "
            f"{r['model_prob']:>6.3f} {r['fair']:>+6d} {r['odds']:>+6d} "
            f"{r['edge']:>+7.3f}{ev_tag}  "
            f"{r['kelly']:>5.1f}%"
        )

    # ── +EV bets ────────────────────────────────────────────────────────────
    print(f"\n{'─' * w}")
    print(f"  +EV BETS  ({len(pos_ev)} found, threshold >{EDGE_THRESHOLD:.0%})")
    print(f"{'─' * w}")
    if pos_ev:
        for r in pos_ev:
            print(
                f"  *** {r['prop']:<20} {r['game']:<12} "
                f"Edge: {r['edge']:+.3f}   "
                f"Model: {r['model_prob']:.3f}   "
                f"Book: {r['odds']:+d}   "
                f"Kelly: {r['kelly']:.1f}%"
            )
    else:
        print("  None.")

    # ── Neutral (small edge) ─────────────────────────────────────────────────
    if neutral:
        print(f"\n{'─' * w}")
        print(f"  MARGINAL  (0 < edge <= {EDGE_THRESHOLD:.0%})")
        print(f"{'─' * w}")
        for r in neutral:
            print(f"  {r['prop']:<20} {r['game']:<12} Edge: {r['edge']:+.3f}   Book: {r['odds']:+d}")

    # ── Worst edges ─────────────────────────────────────────────────────────
    if neg_ev:
        print(f"\n{'─' * w}")
        print("  AVOID  (negative edge)")
        print(f"{'─' * w}")
        for r in neg_ev[:5]:
            print(f"  {r['prop']:<20} {r['game']:<12} Edge: {r['edge']:+.3f}   Book: {r['odds']:+d}")

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{'─' * w}")
    print("  SUMMARY")
    print(f"{'─' * w}")
    print(f"  Props entered    : {len(rated)}")
    print(f"  +EV bets         : {len(pos_ev)}")
    print(f"  Marginal         : {len(neutral)}")
    print(f"  Negative edge    : {len(neg_ev)}")
    if rated:
        best  = max(rated, key=lambda r: r["edge"])
        worst = min(rated, key=lambda r: r["edge"])
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

        home_model = PowerPlayModel(home_row, away_row)
        away_model = PowerPlayModel(away_row, home_row)

        print(f"\n{away} @ {home}")
        print("-" * 30)

        for team, model in ((home, home_model), (away, away_model)):
            over_raw  = input(f"  {team} Over  {DEFAULT_LINE} PP Goals line (press Enter to skip): ")
            under_raw = input(f"  {team} Under {DEFAULT_LINE} PP Goals line (press Enter to skip): ")

            over_prob  = model.probability_over(DEFAULT_LINE)
            under_prob = 1 - over_prob

            print(f"  {team}  Exp goals: {model.expected_goals():.2f}")

            for side, prob, raw in (
                (f"O{DEFAULT_LINE}", over_prob,  over_raw),
                (f"U{DEFAULT_LINE}", under_prob, under_raw),
            ):
                odds = _parse_american_odds(raw)
                fair = model.fair_odds(prob)
                edge = calculate_edge(prob, odds) if odds is not None else None
                kelly = _kelly_pct(prob, odds) * 100 if odds is not None else 0.0

                marker = ""
                if edge is not None:
                    marker = "  *** +EV ***" if edge > EDGE_THRESHOLD else ""

                print(
                    f"    {side:<6}  Model: {prob:.3f}  Fair: {fair:+d}"
                    + (f"  Book: {odds:+d}  Edge: {edge:+.3f}  Kelly: {kelly:.1f}%{marker}" if odds is not None else "")
                )

                results.append({
                    "game":       f"{away}@{home}",
                    "prop":       f"{team} {side}",
                    "model_prob": prob,
                    "fair":       fair,
                    "odds":       odds,
                    "edge":       edge if edge is not None else 0.0,
                    "kelly":      kelly,
                    "_has_odds":  odds is not None,
                })

        print("-" * 40)

    _print_session_report([r for r in results if r["_has_odds"]])


def full_projection_scan() -> None:
    schedule = get_today_schedule()
    if schedule.empty:
        print("\nNo games scheduled for today.")
        return

    stats = get_team_stats()

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

        home_model = PowerPlayModel(home_row, away_row)
        away_model = PowerPlayModel(away_row, home_row)

        print(f"{away} @ {home}")
        for label, model in (("Home", home_model), ("Away", away_model)):
            over_prob  = model.probability_over(DEFAULT_LINE)
            under_prob = 1 - over_prob
            print(f"  {label}  PP opps: {model.project_opportunities():.2f}  "
                  f"Exp goals: {model.expected_goals():.2f}  "
                  f"O{DEFAULT_LINE}: {over_prob:.3f}  "
                  f"U{DEFAULT_LINE}: {under_prob:.3f}")
        print("-" * 40)


def main_menu() -> None:
    menu = {
        "1": ("View Today's Games", view_today_games),
        "2": ("Enter Lines For Today's Games", enter_lines_for_today),
        "3": ("Full Projection Scan", full_projection_scan),
        "4": ("Exit", None),
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
