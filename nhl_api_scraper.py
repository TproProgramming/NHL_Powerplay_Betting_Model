from __future__ import annotations

import requests
import pandas as pd
from datetime import datetime

BASE_WEB   = "https://api-web.nhle.com/v1"
BASE_STATS = "https://api.nhle.com/stats/rest/en"

# Games in a split before it's fully trusted over the overall season total.
# Below this threshold the overall stat is blended in proportionally.
BLEND_THRESHOLD = 30


def _current_season() -> str:
    """Derive the current NHL season ID (e.g. '20252026').

    The NHL season starts in October, so Jan–Sep belongs to the season
    that started the prior calendar year.
    """
    today = datetime.today()
    year  = today.year
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
            if game.get("gameType") != 2:
                continue
            if game.get("gameState") in ("OFF", "FINAL", "CRIT"):
                continue
            rows.append(
                {
                    "away":       game["awayTeam"]["abbrev"],
                    "home":       game["homeTeam"]["abbrev"],
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
        name   = row.get("teamName", {})
        abbrev = row.get("teamAbbrev", {})
        if isinstance(name,   dict): name   = name.get("default", "")
        if isinstance(abbrev, dict): abbrev = abbrev.get("default", "")
        if name and abbrev:
            abbrev_map[str(name)] = str(abbrev)

    return abbrev_map


def _fetch_team_endpoint(
    endpoint:  str,
    season:    str,
    home_road: str | None = None,
) -> pd.DataFrame:
    """Fetch a NHL stats REST endpoint and return its data as a DataFrame.

    Parameters
    ----------
    home_road:
        Optional ``'H'`` (home) or ``'R'`` (road) split filter.
    """
    exp = f"seasonId={season}"
    if home_road:
        exp += f' and homeRoad="{home_road}"'

    resp = requests.get(
        f"{BASE_STATS}/team/{endpoint}",
        params={"cayenneExp": exp, "limit": -1},
        timeout=10,
    )
    resp.raise_for_status()
    return pd.DataFrame(resp.json().get("data", []))


def _blend(df: pd.DataFrame, split_col: str, overall_col: str, games_col: str) -> pd.Series:
    """Weighted blend of a split stat with its season-total equivalent.

    Weight = min(split_games / BLEND_THRESHOLD, 1.0).
    Rows with missing split data fall back to the overall value.
    """
    w = (df[games_col] / BLEND_THRESHOLD).clip(upper=1.0)
    blended = w * df[split_col] + (1 - w) * df[overall_col]
    return blended.fillna(df[overall_col])


def get_team_stats(season: str | None = None) -> pd.DataFrame:
    """Fetch team special-teams stats for the given season, including
    home/road splits blended with season totals.

    Parameters
    ----------
    season:
        NHL season ID string, e.g. ``'20252026'``.  Defaults to the
        current season derived from today's date.

    Returns
    -------
    pd.DataFrame
        Columns: teamId, teamAbbrev, teamFullName,
                 powerPlayPct, penaltyKillPct, ppOpportunitiesPerGame,
                 powerPlayPct_home,  penaltyKillPct_home,  ppOpportunitiesPerGame_home,
                 powerPlayPct_road,  penaltyKillPct_road,  ppOpportunitiesPerGame_road
    """
    if season is None:
        season = _current_season()

    # ── Overall season totals ────────────────────────────────────────────────
    sum_all = _fetch_team_endpoint("summary", season)
    sum_all = sum_all[["teamId", "teamFullName", "powerPlayPct", "penaltyKillPct"]].copy()
    sum_all["teamId"] = sum_all["teamId"].astype(int)

    pp_all = _fetch_team_endpoint("powerplay", season)
    pp_all = pp_all[["teamId", "ppOpportunitiesPerGame"]].copy()
    pp_all["teamId"] = pp_all["teamId"].astype(int)

    df = sum_all.merge(pp_all, on="teamId", how="left")
    df["ppOpportunitiesPerGame"] = df["ppOpportunitiesPerGame"].fillna(3.1)

    # ── Home splits ──────────────────────────────────────────────────────────
    sum_h = _fetch_team_endpoint("summary", season, home_road="H")
    sum_h = (
        sum_h[["teamId", "powerPlayPct", "penaltyKillPct", "gamesPlayed"]]
        .rename(columns={
            "powerPlayPct":  "pp_h",
            "penaltyKillPct": "pk_h",
            "gamesPlayed":    "games_h",
        })
        .copy()
    )
    sum_h["teamId"] = sum_h["teamId"].astype(int)

    pp_h = _fetch_team_endpoint("powerplay", season, home_road="H")
    pp_h = (
        pp_h[["teamId", "ppOpportunitiesPerGame"]]
        .rename(columns={"ppOpportunitiesPerGame": "ppo_h"})
        .copy()
    )
    pp_h["teamId"] = pp_h["teamId"].astype(int)

    # ── Road splits ──────────────────────────────────────────────────────────
    sum_r = _fetch_team_endpoint("summary", season, home_road="R")
    sum_r = (
        sum_r[["teamId", "powerPlayPct", "penaltyKillPct", "gamesPlayed"]]
        .rename(columns={
            "powerPlayPct":  "pp_r",
            "penaltyKillPct": "pk_r",
            "gamesPlayed":    "games_r",
        })
        .copy()
    )
    sum_r["teamId"] = sum_r["teamId"].astype(int)

    pp_r = _fetch_team_endpoint("powerplay", season, home_road="R")
    pp_r = (
        pp_r[["teamId", "ppOpportunitiesPerGame"]]
        .rename(columns={"ppOpportunitiesPerGame": "ppo_r"})
        .copy()
    )
    pp_r["teamId"] = pp_r["teamId"].astype(int)

    # ── Merge splits in ──────────────────────────────────────────────────────
    df = (
        df
        .merge(sum_h, on="teamId", how="left")
        .merge(pp_h,  on="teamId", how="left")
        .merge(sum_r, on="teamId", how="left")
        .merge(pp_r,  on="teamId", how="left")
    )

    # ── Blend splits with season totals ──────────────────────────────────────
    df["powerPlayPct_home"]         = _blend(df, "pp_h",  "powerPlayPct",         "games_h")
    df["penaltyKillPct_home"]       = _blend(df, "pk_h",  "penaltyKillPct",        "games_h")
    df["ppOpportunitiesPerGame_home"] = _blend(df, "ppo_h", "ppOpportunitiesPerGame", "games_h")

    df["powerPlayPct_road"]         = _blend(df, "pp_r",  "powerPlayPct",         "games_r")
    df["penaltyKillPct_road"]       = _blend(df, "pk_r",  "penaltyKillPct",        "games_r")
    df["ppOpportunitiesPerGame_road"] = _blend(df, "ppo_r", "ppOpportunitiesPerGame", "games_r")

    # ── Map teamFullName → teamAbbrev via standings ──────────────────────────
    abbrev_map = _build_abbrev_map()
    df["teamAbbrev"] = df["teamFullName"].map(abbrev_map)

    return df[
        [
            "teamId", "teamAbbrev", "teamFullName",
            "powerPlayPct",         "penaltyKillPct",         "ppOpportunitiesPerGame",
            "powerPlayPct_home",    "penaltyKillPct_home",    "ppOpportunitiesPerGame_home",
            "powerPlayPct_road",    "penaltyKillPct_road",    "ppOpportunitiesPerGame_road",
        ]
    ]
