from app.names import CLOSE, DIFFERENT, EXACT, best_match, classify


def test_suffix_case_and_punctuation_are_ignored():
    assert classify("Kavya Packaging Solutions Pvt Ltd", "KAVYA PACKAGING SOLUTIONS PRIVATE LIMITED") == EXACT
    assert classify("M/s Sharma Traders", "Sharma Traders") == EXACT
    assert classify("Mehta & Sons", "MEHTA AND SONS") == EXACT


def test_same_words_different_order_is_exact():
    assert classify("Traders Sharma", "Sharma Traders") == EXACT


def test_small_typo_is_close_not_exact():
    assert classify("Greenleaf Facility Services", "Greenleaf Facilty Services") == CLOSE


def test_unrelated_and_trade_vs_person_names_differ():
    assert classify("Sharma Electricals", "Rajesh Sharma") == DIFFERENT
    assert classify("Orbit Office Solutions", "Nexora Tech Supplies") == DIFFERENT


def test_empty_names_never_match():
    assert classify("", "Anything") == DIFFERENT and classify(None, None) == DIFFERENT


def test_best_match_picks_strongest_candidate():
    assert best_match("Sharma Electricals", ["RAJESH SHARMA", None, "SHARMA ELECTRICALS"]) == (EXACT, "SHARMA ELECTRICALS")
    assert best_match("x y z", [None, None]) == (DIFFERENT, None)
