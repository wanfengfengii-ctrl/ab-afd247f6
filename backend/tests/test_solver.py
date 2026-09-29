"""Tests for the constraint solver, including brute-force equivalence."""

from __future__ import annotations

import itertools
import random

import pytest

from solver import INF, Exposure, Link, solve


def make(rows, equipment=None, cooldown=None):
    """rows: (name, duration, est, lst) tuples."""
    equipment = equipment or {}
    cooldown = cooldown or {}
    return [
        Exposure(
            name=r[0],
            duration=r[1],
            est=r[2],
            lst=r[3],
            equipment=equipment.get(r[0], "DEV"),
            cooldown=cooldown.get(r[0], 0),
        )
        for r in rows
    ]


def brute_force(exposures, links):
    """Enumerate all integer start vectors; return lexicographic optimum."""
    n = len(exposures)
    best = None

    def ok(starts):
        for i, e in enumerate(exposures):
            if not e.est <= starts[i] <= e.lst:
                return False
        for lk in links:
            gap = starts[lk.b] - starts[lk.a]
            if gap < lk.min_gap:
                return False
            if lk.max_gap < INF and gap > lk.max_gap:
                return False
        res = {}
        for i, e in enumerate(exposures):
            res.setdefault(e.equipment, []).append(i)
        for members in res.values():
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    i, j = members[x], members[y]
                    ei, ej = exposures[i], exposures[j]
                    overlap = not (
                        starts[j] >= starts[i] + ei.duration + ei.cooldown
                        or starts[i] >= starts[j] + ej.duration + ej.cooldown
                    )
                    if overlap:
                        return False
        return True

    ranges = [range(e.est, e.lst + 1) for e in exposures]
    for starts in itertools.product(*ranges):
        if not ok(list(starts)):
            continue
        final_end = max(
            starts[i] + exposures[i].duration for i in range(n)
        )
        key = (final_end, sum(starts), tuple(starts))
        if best is None or key < best[0]:
            best = (key, list(starts))
    return None if best is None else best[1]


def assert_optimal(exposures, links=()):
    result = solve(exposures, list(links))
    ref = brute_force(exposures, list(links))
    if ref is None:
        assert not result.feasible
        assert result.reason == "no_feasible_schedule"
        assert result.starts is None
        return result
    assert result.feasible
    assert result.starts == ref
    assert result.final_end == max(
        ref[i] + exposures[i].duration for i in range(len(exposures))
    )
    return result


def test_simple_shared_equipment_serializes():
    ex = make(
        [
            ("A", 3, 0, 10),
            ("B", 2, 0, 10),
            ("C", 4, 0, 10),
            ("D", 1, 0, 10),
            ("E", 2, 0, 10),
        ]
    )
    r = assert_optimal(ex)
    # minimal makespan is 3+2+4+1+2 = 12; SPT ordering minimizes the sum
    # of starts (D1,B2,E2,A3,C4 -> starts 0,1,3,5,8, sum 17)
    assert r.final_end == 12
    assert sum(r.starts) == 17


def test_cooldown_forces_gap():
    ex = make(
        [
            ("A", 2, 0, 20),
            ("B", 2, 0, 20),
            ("C", 2, 0, 20),
            ("D", 2, 0, 20),
            ("E", 2, 0, 20),
        ],
        cooldown={"A": 3},
    )
    r = solve(ex, [])
    assert r.feasible
    # A must have 3 units of cooldown before the next exposure on DEV
    starts = dict(zip("ABCDE", r.starts))
    others = [v for k, v in starts.items() if k != "A"]
    assert min(others) >= starts["A"] + 5 or starts["A"] >= max(
        v + 2 for k, v in starts.items() if k != "A"
    )


def test_min_max_link_gaps():
    ex = make(
        [
            ("A", 1, 0, 20),
            ("B", 1, 0, 20),
            ("C", 1, 0, 20),
            ("D", 1, 0, 20),
            ("E", 1, 0, 20),
        ],
        equipment={n: "DEV" for n in "ABCDE"},
    )
    links = [
        Link(0, 1, 2, 5),
        Link(1, 2, 0, 3),
        Link(2, 3, 1, 4),
        Link(3, 4, 0, 2),
    ]
    r = assert_optimal(ex, links)
    s = r.starts
    assert 2 <= s[1] - s[0] <= 5
    assert 0 <= s[2] - s[1] <= 3
    assert 1 <= s[3] - s[2] <= 4
    assert 0 <= s[4] - s[3] <= 2


def test_different_equipment_run_in_parallel():
    ex = make(
        [
            ("A", 5, 0, 0),
            ("B", 5, 0, 0),
            ("C", 5, 0, 0),
            ("D", 5, 0, 0),
            ("E", 5, 0, 0),
        ],
        equipment={n: f"DEV-{n}" for n in "ABCDE"},
    )
    r = solve(ex, [])
    assert r.feasible
    assert r.starts == [0, 0, 0, 0, 0]
    assert r.final_end == 5


def test_window_only_infeasible():
    # even ignoring equipment, A must precede B by >=10 but windows forbid it
    ex = make(
        [
            ("A", 1, 0, 1),
            ("B", 1, 0, 1),
            ("C", 1, 0, 10),
            ("D", 1, 0, 10),
            ("E", 1, 0, 10),
        ],
        equipment={n: f"DEV-{n}" for n in "ABCDE"},
    )
    links = [Link(0, 1, 10, INF)]
    r = solve(ex, links)
    assert not r.feasible
    assert r.reason == "no_feasible_schedule"
    assert "时间窗" in r.reason_detail


def test_resource_infeasible_but_temporally_fine():
    # two long exposures on one device, windows too narrow to serialize
    ex = make(
        [
            ("A", 6, 0, 2),
            ("B", 6, 3, 5),
            ("C", 1, 0, 20),
            ("D", 1, 0, 20),
            ("E", 1, 0, 20),
        ]
    )
    r = solve(ex, [])
    assert not r.feasible
    assert r.reason == "no_feasible_schedule"
    assert "设备" in r.reason_detail


def test_max_gap_creates_resource_conflict():
    ex = make(
        [
            ("A", 1, 0, 10),
            ("B", 1, 0, 10),
            ("C", 1, 0, 10),
            ("D", 1, 0, 10),
            ("E", 1, 0, 10),
        ],
        equipment={n: "DEV" for n in "ABCDE"},
    )
    # A and B must start within 1 of each other, but share one device with
    # duration 1 and zero cooldown -> they could be adjacent, feasible;
    # tighten to gap 0 -> impossible (distinct exposures, same start)
    r = solve(ex, [Link(0, 1, 0, 0)])
    assert not r.feasible


def test_lexicographic_objective_prefers_early_first_start():
    # two schedules with equal makespan and sum but different vectors
    ex = make(
        [
            ("A", 2, 0, 10),
            ("B", 2, 0, 10),
            ("C", 2, 0, 10),
            ("D", 2, 0, 10),
            ("E", 2, 0, 10),
        ],
        equipment={"A": "X", "B": "X", "C": "Y", "D": "Y", "E": "Z"},
    )
    r = assert_optimal(ex)
    assert r.starts[0] == 0


def test_margins_reported():
    ex = make(
        [
            ("A", 2, 0, 9),
            ("B", 1, 0, 9),
            ("C", 1, 0, 9),
            ("D", 1, 0, 9),
            ("E", 1, 0, 9),
        ],
        cooldown={"A": 1},
    )
    links = [Link(0, 2, 1, 8)]
    r = solve(ex, links)
    assert r.feasible
    types = {m["type"] for m in r.margins}
    assert types == {"window", "link", "equipment"}
    link_m = next(m for m in r.margins if m["type"] == "link")
    assert link_m["slack_min"] >= 0
    assert link_m["slack_max"] >= 0
    for m in r.margins:
        if m["type"] == "equipment":
            assert m["slack"] >= 0
        if m["type"] == "window":
            assert m["slack_start"] >= 0 and m["slack_end"] >= 0
    eq_m = next(m for m in r.margins if m["type"] == "equipment")
    assert set(r.equipment_order) == {"DEV"}
    assert eq_m["required"] >= 0


def test_equipment_order_reflects_starts():
    ex = make(
        [
            ("A", 1, 5, 5),
            ("B", 1, 0, 0),
            ("C", 1, 2, 2),
            ("D", 1, 9, 9),
            ("E", 1, 0, 10),
        ]
    )
    r = solve(ex, [])
    assert r.feasible
    assert r.equipment_order["DEV"] == ["B", "C", "A", "E", "D"] or (
        # E at earliest 0 would conflict with B; check consistency instead
        r.starts[4] >= 1
    )
    order = r.equipment_order["DEV"]
    pos = {name: k for k, name in enumerate(order)}
    s = r.starts
    names = ["A", "B", "C", "D", "E"]
    for x in range(len(order) - 1):
        i = names.index(order[x])
        j = names.index(order[x + 1])
        assert s[j] >= s[i] + ex[i].duration + ex[i].cooldown


@pytest.mark.parametrize("seed", range(40))
def test_random_instances_match_brute_force(seed):
    rng = random.Random(seed)
    n = rng.randint(5, 6)
    devs = ["X", "Y"]
    exposures = []
    for i in range(n):
        dur = rng.randint(1, 3)
        est = rng.randint(0, 3)
        lst = est + rng.randint(0, 5)
        exposures.append(
            Exposure(
                name=f"E{i}",
                duration=dur,
                est=est,
                lst=lst,
                equipment=rng.choice(devs),
                cooldown=rng.randint(0, 2),
            )
        )
    links = []
    for _ in range(rng.randint(0, 2)):
        a, b = rng.sample(range(n), 2)
        mn = rng.randint(0, 3)
        mx = mn + rng.randint(0, 4)
        links.append(Link(a, b, mn, mx))
    assert_optimal(exposures, links)
