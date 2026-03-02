# NHL Power Play Betting Model

Python CLI tool that pulls live NHL schedule and team special-teams stats from the NHL API, projects power play opportunities and expected goals using a Poisson distribution model, and evaluates sportsbook lines for betting edge. Outputs fair odds, implied probability, quarter-Kelly stake sizing, and a ranked +EV report with optional CSV export.

---

## How It Works

### 1. Data Pipeline

All data is pulled live from two NHL API sources:

| Data | Endpoint |
|---|---|
| Today's schedule | `api-web.nhle.com/v1/schedule/{date}` |
| PP% and PK% | `api.nhle.com/stats/rest/en/team/summary` |
| Penalties taken per game | `api.nhle.com/stats/rest/en/team/penalties` |
| Team abbreviation mapping | `api-web.nhle.com/v1/standings/now` |

Only regular-season games (`gameType == 2`) that have not yet completed are included. The current season ID is derived automatically from today's date — no configuration required.

### 2. Model

For each team in a matchup, the model computes:

**Projected PP opportunities**
```
PP opps = opponent's penaltiesTakenPer60
```
Each team's PP opportunities equal the number of penalties their opponent takes per game, since games are 60 minutes.

**Conversion rate** — blends the team's PP% with the opponent's PK failure rate:
```
conversion rate = (team PP% + (1 − opp PK%)) / 2
```

**Expected PP goals** — Poisson lambda:
```
expected goals = PP opps × conversion rate
```

**Probability over/under 0.5 PP goals** — using the Poisson CDF:
```
P(over 0.5) = 1 − Poisson.CDF(0.5, λ)
P(under 0.5) = 1 − P(over 0.5)
```

### 3. Edge Calculation

```
implied probability = book odds converted to probability
edge = model probability − implied probability
```

A bet is considered **+EV** when `edge > 2%`.

### 4. Kelly Criterion

Quarter-Kelly stake sizing is used for conservative bankroll management:

```
Kelly % = ((b × p − q) / b) × 0.25
```

Where `b` = profit per unit at the given odds, `p` = model probability, `q` = 1 − p. The result is the recommended percentage of bankroll to wager.

---

## Project Structure

```
nhl_powerplay_model/
├── main.py               # CLI menu, line entry, session report, CSV export
├── nhl_api_scraper.py    # NHL API data fetching and team stat assembly
├── powerplay_model.py    # PowerPlayModel class — Poisson projection logic
├── edge_checker.py       # Odds conversion and edge calculation
└── requirements.txt      # Python dependencies
```

---

## Installation

```bash
pip install -r requirements.txt
```

**Dependencies:** `requests`, `pandas`, `numpy`, `scipy`

Python 3.10+ required.

---

## Usage

```bash
python main.py
```

```
====== NHL POWERPLAY MODEL ======
1. View Today's Games
2. Enter Lines For Today's Games
3. Full Projection Scan
4. Exit
```

### Option 1 — View Today's Games
Lists all unplayed regular-season games for today.

### Option 2 — Enter Lines For Today's Games
Steps through each game and prompts for over and under lines (American odds) for each team. Press Enter to skip any prop. After all lines are entered, prints a full session report:

- **Ranked table** — all priced props sorted best → worst edge with model prob, fair odds, book odds, edge, and Kelly %
- **+EV bets** — any prop with edge > 2%, highlighted with Kelly sizing
- **Marginal** — props with a small positive edge (0–2%)
- **Avoid** — negative edge props
- **Summary** — total props entered, counts by category, best/worst/avg edge

At the end you are prompted to export the report to CSV.

### Option 3 — Full Projection Scan
Runs projections for every game without entering lines. Shows projected PP opportunities, expected PP goals, and over/under 0.5 probability for both teams in each matchup.

---

## CSV Export

When prompted after the session report, typing `y` saves a file named `pp_report_YYYY-MM-DD.csv` to the project directory.

| Column | Description |
|---|---|
| `date` | Report date |
| `game` | Matchup (e.g. `VAN@DAL`) |
| `prop` | Team and side (e.g. `DAL o0.5`) |
| `model_prob` | Model's probability for this outcome |
| `fair_odds` | Fair American odds derived from model |
| `book_odds` | Sportsbook line entered |
| `implied_prob` | Sportsbook's implied probability |
| `edge` | Model prob − implied prob |
| `kelly_pct` | Quarter-Kelly recommended stake (% of bankroll) |
| `ev_flag` | `+EV`, `MARGINAL`, or `AVOID` |

A summary row is appended at the bottom with prop counts, average edge, and best/worst callouts.

---

## Configuration

Three constants at the top of `main.py` can be adjusted:

| Constant | Default | Description |
|---|---|---|
| `EDGE_THRESHOLD` | `0.02` | Minimum edge to flag a bet as +EV |
| `DEFAULT_LINE` | `0.5` | PP goals line used throughout |
| `KELLY_FRACTION` | `0.25` | Fraction of full Kelly (0.25 = quarter-Kelly) |

---

## Limitations

- Model uses season-to-date stats. Early in the season (< ~15 games), small sample sizes make PP% and PK% noisy.
- `penaltiesTakenPer60` covers all penalty types. Majors and misconducts do not produce standard 2-minute power plays but are a small fraction of total penalties.
- No adjustment for goaltender matchup, score state, or referee tendencies (though `ref_multiplier` is available on `PowerPlayModel` for manual adjustment).
- Sportsbook lines must be entered manually — no odds feed integration.
