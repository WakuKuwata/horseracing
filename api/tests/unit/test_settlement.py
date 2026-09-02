"""Fixed hand-calculated examples for the pure JRA settlement matcher."""

from horseracing_api.settlement import is_hit

# Horse 3 won, 7 was second, 9 was third, and 12 was fourth.
FINISH = {3: 1, 7: 2, 9: 3, 12: 4}


def test_win_hit_and_miss() -> None:
    assert is_hit("win", [3], FINISH, 8) is True  # 3 is first.
    assert is_hit("win", [7], FINISH, 8) is False  # 7 is second.


def test_exacta_requires_finishing_order() -> None:
    assert is_hit("exacta", [3, 7], FINISH, 8) is True  # First -> second.
    assert is_hit("exacta", [7, 3], FINISH, 8) is False  # Reverse order loses.


def test_quinella_matches_the_top_two_set() -> None:
    assert is_hit("quinella", [3, 7], FINISH, 8) is True  # {first, second}.
    assert is_hit("quinella", [3, 9], FINISH, 8) is False  # Third replaces second.


def test_trifecta_requires_finishing_order() -> None:
    assert is_hit("trifecta", [3, 7, 9], FINISH, 8) is True  # First -> second -> third.
    assert is_hit("trifecta", [3, 9, 7], FINISH, 8) is False  # Second/third reversed.


def test_trio_matches_the_top_three_set() -> None:
    assert is_hit("trio", [3, 7, 9], FINISH, 8) is True  # {first, second, third}.
    assert is_hit("trio", [3, 7, 12], FINISH, 8) is False  # Fourth replaces third.


def test_wide_all_three_top_three_pairs_hit() -> None:
    assert is_hit("wide", [3, 7], FINISH, 8) is True  # First + second.
    assert is_hit("wide", [3, 9], FINISH, 8) is True  # First + third.
    assert is_hit("wide", [7, 9], FINISH, 8) is True  # Second + third.
    assert is_hit("wide", [3, 12], FINISH, 8) is False  # Fourth is outside the top three.


def test_place_started_horse_boundaries() -> None:
    assert is_hit("place", [3], FINISH, 4) is None  # Four starters: place is not sold.
    assert is_hit("place", [7], FINISH, 5) is True  # Five starters: second is in top two.
    assert is_hit("place", [9], FINISH, 5) is False  # Five starters: third is outside.
    assert is_hit("place", [7], FINISH, 7) is True  # Seven starters: still top two.
    assert is_hit("place", [9], FINISH, 7) is False  # Seven starters: third is outside.
    assert is_hit("place", [9], FINISH, 8) is True  # Eight starters: top three pays.
    assert is_hit("place", [12], FINISH, 8) is False  # Eight starters: fourth is outside.


def test_first_place_dead_heat_allows_both_exacta_orders() -> None:
    # Horses 3 and 7 share first; either assignment to the first two slots is official.
    dead_heat = {3: 1, 7: 1, 9: 3, 12: 4}

    assert is_hit("exacta", [3, 7], dead_heat, 8) is True
    assert is_hit("exacta", [7, 3], dead_heat, 8) is True
    assert is_hit("exacta", [3, 9], dead_heat, 8) is False  # Omits co-winner 7.
    assert is_hit("trifecta", [3, 7, 9], dead_heat, 8) is True
    assert is_hit("trifecta", [7, 3, 9], dead_heat, 8) is True


def test_trio_boundary_dead_heat_pays_every_occupiable_combination() -> None:
    # 3着同着(競技順位 1,2,3,3 — 同着の次は 5 位): JRA は枠を占められる 3 頭組を
    # すべて的中とする。{1st,2nd,3a} と {1st,2nd,3b} の両方が的中で、
    # 2 着馬を欠く組は(3 着 2 頭を両方入れても)不的中。
    dead_heat = {3: 1, 7: 2, 9: 3, 12: 3, 15: 5}

    assert is_hit("trio", [3, 7, 9], dead_heat, 8) is True
    assert is_hit("trio", [3, 7, 12], dead_heat, 8) is True
    assert is_hit("trio", [3, 9, 12], dead_heat, 8) is False   # omits the 2nd-place horse
    assert is_hit("trio", [3, 7, 9, 12], dead_heat, 8) is False  # a trio is exactly 3 horses


def test_quinella_second_place_dead_heat_pays_both_pairs() -> None:
    # 2着同着(競技順位 1,2,2,4): 馬連は (1着,2a) と (1着,2b) の両方が的中。
    # 同着 2 頭同士の組は 1 着馬の枠を占められないので不的中。
    dead_heat = {3: 1, 7: 2, 9: 2, 12: 4}

    assert is_hit("quinella", [3, 7], dead_heat, 8) is True
    assert is_hit("quinella", [3, 9], dead_heat, 8) is True
    assert is_hit("quinella", [7, 9], dead_heat, 8) is False
