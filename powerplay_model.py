import numpy as np
from scipy.stats import poisson


class PowerPlayModel:

    def __init__(self, team_row, opp_row, ref_multiplier=1.0):

        # API returns these as decimals (0.155 = 15.5%) — no /100 needed
        self.team_pp = team_row["powerPlayPct"]
        self.team_pk = team_row["penaltyKillPct"]
        self.team_penalties = team_row["penaltiesPerGame"]

        self.opp_pp = opp_row["powerPlayPct"]
        self.opp_pk = opp_row["penaltyKillPct"]
        self.opp_penalties = opp_row["penaltiesPerGame"]

        self.ref_multiplier = ref_multiplier

    # Estimate Powerplay Opportunities
    # This team's PPs = how many penalties the opponent takes per game
    def project_opportunities(self):
        return self.opp_penalties * self.ref_multiplier

    # Blend PP% and Opponent PK%
    def conversion_rate(self):
        opp_pk_failure = 1 - self.opp_pk
        return (self.team_pp + opp_pk_failure) / 2

    # Expected Goals (Lambda)
    def expected_goals(self):
        return self.project_opportunities() * self.conversion_rate()

    # Probability Over Line
    def probability_over(self, line):
        lam = self.expected_goals()
        return 1 - poisson.cdf(line, lam)

    # Convert to Fair American Odds
    def fair_odds(self, probability):
        if probability > 0.5:
            return -round((probability / (1 - probability)) * 100)
        else:
            return round(((1 - probability) / probability) * 100)