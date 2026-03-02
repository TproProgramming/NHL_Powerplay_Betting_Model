from __future__ import annotations

import numpy as np
from scipy.stats import poisson


class PowerPlayModel:

    def __init__(
        self,
        team_row,
        opp_row,
        ref_multiplier: float = 1.0,
        league_avg_pk_fail: float = 0.20,
    ):
        # API returns these as decimals (0.155 = 15.5%) — no /100 needed
        self.team_pp  = team_row["powerPlayPct"]
        self.team_pk  = team_row["penaltyKillPct"]
        self.team_ppo = team_row["ppOpportunitiesPerGame"]

        self.opp_pp  = opp_row["powerPlayPct"]
        self.opp_pk  = opp_row["penaltyKillPct"]
        self.opp_ppo = opp_row["ppOpportunitiesPerGame"]

        self.ref_multiplier    = ref_multiplier
        self.league_avg_pk_fail = league_avg_pk_fail

    def project_opportunities(self) -> float:
        # Use this team's own historical PP drawing rate — already excludes
        # offsetting minors since the API only counts actual PP starts.
        return self.team_ppo * self.ref_multiplier

    def conversion_rate(self) -> float:
        # Start from team's own PP% (their true scoring rate per opportunity),
        # then adjust by how much the opponent's PK failure rate deviates from
        # the league average.  This avoids double-counting while still rewarding
        # good PP units facing weak penalty kills and penalising them against
        # strong ones.
        opp_pk_deviation = (1 - self.opp_pk) - self.league_avg_pk_fail
        return max(self.team_pp + opp_pk_deviation, 0.01)

    def expected_goals(self) -> float:
        return self.project_opportunities() * self.conversion_rate()

    def probability_over(self, line: float) -> float:
        return 1 - poisson.cdf(line, self.expected_goals())

    def fair_odds(self, probability: float) -> int:
        if probability > 0.5:
            return -round((probability / (1 - probability)) * 100)
        else:
            return round(((1 - probability) / probability) * 100)
