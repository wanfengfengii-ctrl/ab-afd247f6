"""Integer scheduling solver for synchrotron beamline exposures.

Determines integer start times for a batch of exposures subject to:

* release / due time windows   est[i] <= s[i] <= lst[i]
* detector occupancy + cooldown on shared equipment
  s[j] >= s[i] + duration[i] + cooldown[i]   (or the symmetric order)
* min / max sequencing gaps between ordered exposures
  min_gap <= s[b] - s[a] <= max_gap

The solver performs a lexicographic optimization on

    (makespan, sum of starts, start vector in input order)

i.e. it first minimizes the final end time, then the sum of start times,
then lexicographically the start sequence itself.

Algorithm.  Temporal requirements are difference constraints
``s[to] >= s[frm] + w``; bounds propagation (FIFO queues, forward and
backward) maintains earliest/latest start domains.  Exposures sharing a
piece of equipment form a disjunctive resource: for each pair the two
possible serializations are tested against the current domains, an
impossible order is removed, and when exactly one order survives the
corresponding precedence arc (including the predecessor's cooldown) is
installed.  Search branches on still-open pair orderings.  Every
objective is componentwise monotone in the start times, so once all
resource orders are settled the propagated earliest starts are the
unique optimum for that ordering; lexicographic lower-bound pruning then
discards every dominated node.

Everything is pure Python on purpose: no native solver is required to
build or verify the project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Sentinel for "no maximum gap" constraints.
INF = 10**12


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Exposure:
    name: str
    duration: int
    est: int          # earliest start (inclusive)
    lst: int          # latest start (inclusive)
    equipment: str
    cooldown: int      # cooldown after this exposure ends


@dataclass(frozen=True)
class Link:
    a: int             # index of predecessor exposure
    b: int             # index of successor exposure
    min_gap: int       # s[b] - s[a] >= min_gap
    max_gap: int       # s[b] - s[a] <= max_gap (INF == unbounded)


@dataclass
class SolveResult:
    feasible: bool
    starts: Optional[list[int]] = None
    final_end: Optional[int] = None
    equipment_order: dict[str, list[str]] = field(default_factory=dict)
    margins: list[dict] = field(default_factory=list)
    reason: Optional[str] = None          # machine-readable infeasibility
    reason_detail: Optional[str] = None   # human-readable detail
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------


class Solver:
    def __init__(self, exposures: list[Exposure], links: list[Link]):
        self.exposures = exposures
        self.links = links
        self.n = len(exposures)

        # equipment -> exposure indices (input order preserved)
        self.resources: dict[str, list[int]] = {}
        for i, ex in enumerate(exposures):
            self.resources.setdefault(ex.equipment, []).append(i)

        # unordered same-equipment pairs with required serial gaps
        # key (i, j), i < j  ->  (gap if i->j, gap if j->i)
        self.pairs: dict[tuple[int, int], tuple[int, int]] = {}
        for members in self.resources.values():
            for x in range(len(members)):
                i = members[x]
                pi = exposures[i].duration + exposures[i].cooldown
                for y in range(x + 1, len(members)):
                    j = members[y]
                    pj = exposures[j].duration + exposures[j].cooldown
                    self.pairs[(min(i, j), max(i, j))] = (pi, pj)

        # base temporal arcs coming from the links only:
        #   min gap:  s[b] >= s[a] + min_gap
        #   max gap:  s[a] >= s[b] - max_gap
        self.base_out: list[list[tuple[int, int]]] = [
            [] for _ in range(self.n)
        ]
        self.base_in: list[list[tuple[int, int]]] = [
            [] for _ in range(self.n)
        ]
        for lk in links:
            self.base_out[lk.a].append((lk.b, lk.min_gap))
            self.base_in[lk.b].append((lk.a, lk.min_gap))
            if lk.max_gap < INF:
                self.base_out[lk.b].append((lk.a, -lk.max_gap))
                self.base_in[lk.a].append((lk.b, -lk.max_gap))

        self.nodes = 0
        self.best: Optional[list[int]] = None
        self.best_key: Optional[tuple] = None

    # -- propagation ---------------------------------------------------

    def _propagate(
        self,
        lo: list[int],
        hi: list[int],
        orders: dict[tuple[int, int], str],
    ) -> bool:
        """Narrow domains given fixed resource pair orders.

        Mutates ``lo``/``hi`` in place and ``orders`` when a pair order
        becomes forced.  Returns False on contradiction.
        """
        out = [list(arcs) for arcs in self.base_out]
        inn = [list(arcs) for arcs in self.base_in]
        for (i, j), tag in orders.items():
            pi, pj = self.pairs[(i, j)]
            if tag == f"{i}-{j}":
                out[i].append((j, pi))
                inn[j].append((i, pi))
            else:
                out[j].append((i, pj))
                inn[i].append((j, pj))

        for _ in range(256):
            # forward: earliest starts, s[to] >= s[frm] + w
            queue = list(range(self.n))
            seen = [True] * self.n
            while queue:
                i = queue.pop()
                seen[i] = False
                for j, w in out[i]:
                    v = lo[i] + w
                    if v > lo[j]:
                        # A positive-weight cycle (e.g. a max-gap link
                        # contradicting a forced resource order) would pump
                        # this without bound: detect contradiction at once.
                        if v > hi[j]:
                            return False
                        lo[j] = v
                        if not seen[j]:
                            seen[j] = True
                            queue.append(j)
            # backward: latest starts
            queue = list(range(self.n))
            seen = [True] * self.n
            while queue:
                j = queue.pop()
                seen[j] = False
                for i, w in inn[j]:
                    v = hi[j] - w
                    if v < hi[i]:
                        if v < lo[i]:
                            return False
                        hi[i] = v
                        if not seen[i]:
                            seen[i] = True
                            queue.append(i)

            if any(lo[i] > hi[i] for i in range(self.n)):
                return False

            # disjunctive pair filtering against narrowed domains
            changed = False
            for (i, j), (pi, pj) in self.pairs.items():
                tag = orders.get((i, j))
                # i before j feasible iff some s_i,s_j with s_j>=s_i+pi
                can_ij = lo[i] + pi <= hi[j]
                can_ji = lo[j] + pj <= hi[i]
                if tag == f"{i}-{j}":
                    can_ji = False
                elif tag == f"{j}-{i}":
                    can_ij = False
                if not can_ij and not can_ji:
                    return False
                if can_ij and not can_ji:
                    if tag != f"{i}-{j}":
                        orders[(i, j)] = f"{i}-{j}"
                        out[i].append((j, pi))
                        inn[j].append((i, pi))
                        changed = True
                elif can_ji and not can_ij:
                    if tag != f"{j}-{i}":
                        orders[(i, j)] = f"{j}-{i}"
                        out[j].append((i, pj))
                        inn[i].append((j, pj))
                        changed = True
            if not changed:
                return True
        return True

    # -- search --------------------------------------------------------

    def solve(self) -> SolveResult:
        lo0 = [e.est for e in self.exposures]
        hi0 = [e.lst for e in self.exposures]
        orders0: dict[tuple[int, int], str] = {}

        if not self._propagate(lo0, hi0, orders0):
            return self._infeasible()

        prefixes0 = {equip: [] for equip in self.resources}
        self._search(lo0, hi0, orders0, prefixes0)

        if self.best is None:
            return self._infeasible()
        return self._build_result()

    def _search(
        self,
        lo: list[int],
        hi: list[int],
        orders: dict[tuple[int, int], str],
        prefixes: dict[str, list[int]],
    ) -> None:
        self.nodes += 1

        # prune against the incumbent using structural lower bounds
        if self.best_key is not None:
            end_lb, sum_lb = self._bounds(lo, prefixes)
            if end_lb > self.best_key[0]:
                return
            if end_lb == self.best_key[0] and sum_lb > self.best_key[1]:
                return
            if (
                end_lb == self.best_key[0]
                and sum_lb == self.best_key[1]
                and tuple(lo) > self.best_key[2]
            ):
                return

        # pick the resource with the most unscheduled exposures: branching
        # "which exposure runs next on this resource" builds the actual
        # permutation, so each feasible total order has exactly one path
        todo = [
            (len(members) - len(prefixes[equip]), equip)
            for equip, members in self.resources.items()
            if len(prefixes[equip]) < len(members)
        ]
        if not todo:
            # every resource fully serialized: earliest starts are optimal
            starts = lo[:]
            final_end = max(
                starts[i] + self.exposures[i].duration
                for i in range(self.n)
            )
            key = (final_end, sum(starts), tuple(starts))
            if self.best_key is None or key < self.best_key:
                self.best_key = key
                self.best = starts
            return

        _, equip = max(todo)
        members = self.resources[equip]
        prefix = prefixes[equip]
        placed = set(prefix)
        remaining = [i for i in members if i not in placed]

        # an exposure forced (by propagation) behind another unscheduled
        # exposure cannot be the next one on this resource
        def eligible(j: int) -> bool:
            for k in remaining:
                if k == j:
                    continue
                tag = orders.get((min(j, k), max(j, k)))
                if tag is not None and tag != f"{j}-{k}":
                    return False
            return True

        # early-start then SPT ordering finds the lexicographic optimum fast
        candidates = sorted(
            remaining,
            key=lambda j: (
                lo[j],
                self.exposures[j].duration + self.exposures[j].cooldown,
                j,
            ),
        )

        for j in candidates:
            if not eligible(j):
                continue
            lo2, hi2 = lo[:], hi[:]
            orders2 = dict(orders)
            key_changed = False
            for k in remaining:
                if k == j:
                    continue
                pair = (min(j, k), max(j, k))
                tag = f"{j}-{k}"
                if orders2.get(pair) != tag:
                    orders2[pair] = tag
                    key_changed = True
            if not key_changed:
                # pair arcs already installed; just descend
                if self._propagate(lo2, hi2, orders2):
                    prefixes2 = dict(prefixes)
                    prefixes2[equip] = prefix + [j]
                    self._search(lo2, hi2, orders2, prefixes2)
                continue
            if self._propagate(lo2, hi2, orders2):
                prefixes2 = dict(prefixes)
                prefixes2[equip] = prefix + [j]
                self._search(lo2, hi2, orders2, prefixes2)

    def _bounds(
        self,
        lo: list[int],
        prefixes: dict[str, list[int]],
    ) -> tuple[int, int]:
        """Lexicographic lower bounds (final end, sum of starts).

        Per resource the fixed prefix already appears in ``lo``; for the
        unscheduled suffix we use, independently per resource, the serial
        workload bound: completion cannot beat cumulative processing time,
        and the sum of starts cannot beat shortest-processing-time slots
        (both ignoring release times, hence valid even with windows /
        cross-resource links, whose effects are separately present in lo).
        """
        end_lb = max(
            lo[i] + self.exposures[i].duration for i in range(self.n)
        )
        sum_lb = 0
        for equip, members in self.resources.items():
            prefix = prefixes[equip]
            for i in prefix:
                sum_lb += lo[i]
            remaining = [i for i in members if i not in set(prefix)]
            if not remaining:
                continue
            if prefix:
                last = prefix[-1]
                cur = (
                    lo[last]
                    + self.exposures[last].duration
                    + self.exposures[last].cooldown
                )
            else:
                cur = 0
            procs = sorted(
                self.exposures[i].duration + self.exposures[i].cooldown
                for i in remaining
            )
            cooldowns = [self.exposures[i].cooldown for i in remaining]
            # makespan: all but the final cooldown must elapse
            end_lb = max(
                end_lb,
                cur + sum(procs) - max(cooldowns),
                max(lo[i] + self.exposures[i].duration for i in remaining),
            )
            # sum of starts: max of per-exposure lower bounds and SPT slots
            slot_sum = 0
            acc = cur
            for k in range(len(procs)):
                slot_sum += acc
                acc += procs[k]
            sum_lb += max(sum(lo[i] for i in remaining), slot_sum)
        return end_lb, sum_lb

    # -- infeasibility diagnosis --------------------------------------

    def _infeasible(self) -> SolveResult:
        # Temporal network alone (windows + links), ignoring equipment:
        # Bellman-Ford relaxation of difference constraints.
        n = self.n
        lo = [e.est for e in self.exposures]
        hi = [e.lst for e in self.exposures]
        for _ in range(n + 2):
            changed = False
            for i in range(n):
                for j, w in self.base_out[i]:
                    if lo[j] < lo[i] + w:
                        lo[j] = lo[i] + w
                        changed = True
            if not changed:
                break
        if any(lo[i] > hi[i] for i in range(n)):
            return SolveResult(
                feasible=False,
                reason="no_feasible_schedule",
                reason_detail=(
                    "时间窗与衔接间隔本身相互矛盾：即使所有设备都可独占使用，"
                    "也不存在满足全部最早/最晚开始时刻与最小/最大衔接间隔的"
                    "整数开始时刻。"
                ),
                stats={"nodes": self.nodes},
            )

        detail = self._resource_conflict_detail(lo, hi)
        return SolveResult(
            feasible=False,
            reason="no_feasible_schedule",
            reason_detail=detail,
            stats={"nodes": self.nodes},
        )

    def _resource_conflict_detail(
        self, lo: list[int], hi: list[int]
    ) -> str:
        worst = None
        for equip, members in self.resources.items():
            if len(members) < 2:
                continue
            need = sum(
                self.exposures[i].duration
                + self.exposures[i].cooldown
                for i in members
            )
            # trailing cooldown of the final exposure need not fit inside
            # any window, so subtract the smallest one for the message
            tail = min(self.exposures[i].cooldown for i in members)
            usable = need - tail
            span_lo = min(lo[i] for i in members)
            span_hi = max(
                hi[i] + self.exposures[i].duration for i in members
            )
            capacity = span_hi - span_lo
            if worst is None or need > worst[1]:
                worst = (equip, need, capacity, members, usable)
        if worst:
            equip, need, capacity, members, usable = worst
            names = "、".join(self.exposures[i].name for i in members)
            return (
                f"时间窗与衔接约束本身可满足，但设备「{equip}」无法串行排下："
                f"其上的曝光（{names}）占用与冷却合计需 {need} 个时间单位"
                f"（末项冷却不占排程容量，仍至少需 {usable}），"
                f"而各时间窗在该设备上的可用跨度仅约 {capacity}，"
                f"共用设备冲突无法消除。"
            )
        return (
            "时间窗与衔接约束均可满足，但共用设备占用与冷却无法在"
            "给定时间窗内完全错开。"
        )

    # -- result assembly ----------------------------------------------

    def _build_result(self) -> SolveResult:
        assert self.best is not None and self.best_key is not None
        starts = self.best
        ex = self.exposures
        final_end = self.best_key[0]

        order: dict[str, list[int]] = {}
        for equip, members in self.resources.items():
            order[equip] = sorted(members, key=lambda i: (starts[i], i))

        margins: list[dict] = []

        # time-window margins
        for i, e in enumerate(ex):
            margins.append(
                {
                    "type": "window",
                    "exposure": e.name,
                    "index": i,
                    "description": f"{e.name} 的时间窗余量",
                    "earliest": e.est,
                    "latest": e.lst,
                    "start": starts[i],
                    "end": starts[i] + e.duration,
                    "slack_start": starts[i] - e.est,
                    "slack_end": e.lst - starts[i],
                }
            )

        # sequencing-link margins
        for lk in self.links:
            gap = starts[lk.b] - starts[lk.a]
            margins.append(
                {
                    "type": "link",
                    "a": ex[lk.a].name,
                    "b": ex[lk.b].name,
                    "a_index": lk.a,
                    "b_index": lk.b,
                    "description": f"{ex[lk.a].name} → {ex[lk.b].name} 衔接余量",
                    "gap": gap,
                    "min_gap": lk.min_gap,
                    "max_gap": None if lk.max_gap >= INF else lk.max_gap,
                    "slack_min": gap - lk.min_gap,
                    "slack_max": None
                    if lk.max_gap >= INF
                    else lk.max_gap - gap,
                }
            )

        # equipment adjacency margins (occupancy + predecessor cooldown)
        for equip, seq in order.items():
            for pos in range(len(seq) - 1):
                i, k = seq[pos], seq[pos + 1]
                ei = ex[i]
                required = ei.duration + ei.cooldown
                gap = starts[k] - starts[i]
                margins.append(
                    {
                        "type": "equipment",
                        "equipment": equip,
                        "a": ei.name,
                        "b": ex[k].name,
                        "description": (
                            f"设备「{equip}」：{ei.name} → {ex[k].name} "
                            f"占用/冷却余量"
                        ),
                        "gap": gap,
                        "required": required,
                        "slack": gap - required,
                    }
                )

        return SolveResult(
            feasible=True,
            starts=starts,
            final_end=final_end,
            equipment_order={
                equip: [self.exposures[i].name for i in seq]
                for equip, seq in order.items()
            },
            margins=margins,
            stats={
                "nodes": self.nodes,
                "objective": {
                    "final_end": final_end,
                    "sum_starts": sum(starts),
                    "start_vector": starts,
                },
            },
        )


def solve(exposures: list[Exposure], links: list[Link]) -> SolveResult:
    return Solver(exposures, links).solve()
