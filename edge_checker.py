def american_to_implied(odds):
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def calculate_edge(model_probability, sportsbook_odds):
    implied = american_to_implied(sportsbook_odds)
    return model_probability - implied