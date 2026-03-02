from __future__ import annotations

import requests
import pandas as pd
from datetime import datetime

BASE_WEB = "https://api-web.nhle.com/v1"
BASE_STATS = "https://api.nhle.com/stats/rest/en"


def _current_season() -> str:
    """Derive the current NHL season ID (e.g. '20252026').

    The NHL season starts in October, so Jan–Sep belongs to the season
    that started the prior calendar year.
    """
    today = datetime.today()
    year = today.year
    if today.month >= 10:
        return f"{year}{year + 1}"
    return f"{year - 1}{year}"


def get_today_schedule() -> pd.DataFrame:
    """Return today's unplayed regular-season games.

    Returns
    -------
    pd.DataFrame
        Columns: away, home, awayTeamId, homeTeamId
        Empty DataFrame if no qualifying games exist.
    """
    today = datetime.today().strftime("%Y-%m-%d")

    resp = requests.get(f"{BASE_WEB}/schedule/{today}", timeout=10)
    resp.raise_for_status()
    data = resp.json()

    rows: list[dict] = []
    for day in data.get("gameWeek", []):
        if day.get("date") != today:
            continue
        for game in day.get("games", []):
            # Regular season only
            if game.get("gameType") != 2:
                continue
            # Skip completed games
            if game.get("gameState") in ("OFF", "FINAL", "CRIT"):
                continue
            rows.append(
                {
                    "away": game["awayTeam"]["abbrev"],
                    "home": game["homeTeam"]["abbrev"],
                    "awayTeamId": int(game["awayTeam"]["id"]),
                    "homeTeamId": int(game["homeTeam"]["id"]),
                }
            )

    return pd.DataFrame(rows, columns=["away", "home", "awayTeamId", "homeTeamId"])


def _build_abbrev_map() -> dict[str, str]:
    """Return {teamFullName: teamAbbrev} mapping pulled from the standings endpoint.

    The standings endpoint has no teamId field; teamFullName is the shared
    key between it and the stats REST API.  teamAbbrev is a locale object
    in the v1 API, e.g. {"default": "TOR"}.
    """
    resp = requests.get(f"{BASE_WEB}/standings/now", timeout=10)
    resp.raise_for_status()

    abbrev_map: dict[str, str] = {}
    for row in resp.json().get("standings", []):
        name = row.get("teamName", {})
        if isinstance(name, dict):
            name = name.get("default", "")
        abbrev = row.get("teamAbbrev", {})
        if isinstance(abbrev, dict):
            abbrev = abbrev.get("default", "")
        if name and abbrev:
            abbrev_map[str(name)] = str(abbrev)

    return abbrev_map


def _fetch_team_endpoint(endpoint: str, season: str) -> pd.DataFrame:
    """Fetch a NHL stats REST endpoint and return its data as a DataFrame."""
    resp = requests.get(
        f"{BASE_STATS}/team/{endpoint}",
        params={"cayenneExp": f"seasonId={season}", "limit": -1},
        timeout=10,
    )
    resp.raise_for_status()
    return pd.DataFrame(resp.json().get("data", []))


def get_team_stats(season: str | None = None) -> pd.DataFrame:
    """Fetch team special-teams stats for the given season.

    Parameters
    ----------
    season:
        NHL season ID string, e.g. ``'20252026'``.  Defaults to the
        current season derived from today's date.

    Returns
    -------
    pd.DataFrame
        Columns: teamId, teamAbbrev, teamFullName,
                 powerPlayPct, penaltyKillPct, ppOpportunitiesPerGame
    """
    if season is None:
        season = _current_season()

    # PP% and PK% live in the summary endpoint
    summary = _fetch_team_endpoint("summary", season)
    summary = summary[
        ["teamId", "teamFullName", "powerPlayPct", "penaltyKillPct"]
    ].copy()
    summary["teamId"] = summary["teamId"].astype(int)

    # Actual PP opportunities received per game — already excludes offsetting minors
    # because the scorekeeper only logs a ppOpportunity when a power play actually starts.
    pp_raw = _fetch_team_endpoint("powerplay", season)
    if "ppOpportunitiesPerGame" in pp_raw.columns:
        pp_raw = pp_raw[["teamId", "ppOpportunitiesPerGame"]].copy()
        pp_raw["teamId"] = pp_raw["teamId"].astype(int)
        df = summary.merge(pp_raw, on="teamId", how="left")
    else:
        df = summary.copy()
        df["ppOpportunitiesPerGame"] = float("nan")

    # Fill any gaps with league average
    df["ppOpportunitiesPerGame"] = df["ppOpportunitiesPerGame"].fillna(3.1)

    # Map teamFullName → teamAbbrev via standings
    abbrev_map = _build_abbrev_map()
    df["teamAbbrev"] = df["teamFullName"].map(abbrev_map)

    return df[
        [
            "teamId",
            "teamAbbrev",
            "teamFullName",
            "powerPlayPct",
            "penaltyKillPct",
            "ppOpportunitiesPerGame",
        ]
    ]
