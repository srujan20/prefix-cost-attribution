"""One audit: serve the workload, allocate it five ways, and return one of three verdicts.

The verdicts are about the shipped scheme rather than about the workload, which is
the right subject: a workload cannot be wrong, and a scheme can be.

`attribution-sound`, exit 0. The shipped scheme sums to what the server spent and
produces the same shares under every ordering tested. That is a bill.

`order-dependent`, exit 1. The scheme sums to the spend and its shares move
between orderings by more than the configured threshold. A tenant is being charged
differently for identical usage depending on who arrived first, and the report
names the tenant with the widest swing.

`not-an-attribution`, exit 2. The scheme does not sum to what the server spent, so
the question of ordering does not arise: whatever it is dividing, it is not the
bill. This is the state a per request token count is always in once a prefix cache
is switched on, and returning success for it would certify a number that is not a
division of anything.

Ordering stability is measured rather than assumed even for the schemes that are
order independent by construction. Those come out at exactly zero, and a
construction that is supposed to give exactly zero is worth checking rather than
trusting, because it is the kind of claim that survives a refactor by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .attribution import Allocation, allocate_all
from .config import Policy
from .serving import ServingResult, serve
from .trie import PrefixTrie, build_trie
from .workload import Workload, orderings


class Verdict(str, Enum):
    SOUND = "attribution-sound"
    ORDER_DEPENDENT = "order-dependent"
    NOT_AN_ATTRIBUTION = "not-an-attribution"

    @property
    def exit_code(self) -> int:
        return {
            Verdict.SOUND: 0,
            Verdict.ORDER_DEPENDENT: 1,
            Verdict.NOT_AN_ATTRIBUTION: 2,
        }[self]


@dataclass(frozen=True)
class Stability:
    """How much a scheme's prefill shares move between orderings of one workload.

    Measured on the prefill shares rather than on the whole bill, because decode
    is never shared and every scheme agrees about it. Including it would divide
    the disputed quantity by a much larger number and report that the dispute is
    small, which is true of the bill and false of the thing being argued over.
    """

    scheme: str
    orderings: int
    max_relative_spread: float
    mean_relative_spread: float
    worst_tenant: int

    @property
    def exactly_stable(self) -> bool:
        return self.max_relative_spread == 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "scheme": self.scheme,
            "orderings": self.orderings,
            "max_relative_spread": self.max_relative_spread,
            "mean_relative_spread": self.mean_relative_spread,
            "worst_tenant": self.worst_tenant,
            "exactly_stable": self.exactly_stable,
        }


@dataclass(frozen=True)
class AuditResult:
    verdict: Verdict
    scheme: str
    allocations: dict[str, Allocation]
    stability: dict[str, Stability]
    result: ServingResult
    trie_nodes: int
    shared_nodes: int
    actual_cost: float
    seed: int

    @property
    def over_attribution(self) -> float:
        """What the shipped per request bill charges, over what the server spent."""
        return self.allocations["per_request"].total / self.actual_cost

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "exit_code": self.verdict.exit_code,
            "scheme": self.scheme,
            "allocations": {name: item.as_dict() for name, item in self.allocations.items()},
            "stability": {name: item.as_dict() for name, item in self.stability.items()},
            "serving": self.result.as_dict(),
            "trie_nodes": self.trie_nodes,
            "shared_nodes": self.shared_nodes,
            "actual_cost": self.actual_cost,
            "over_attribution": self.over_attribution,
            "seed": self.seed,
        }


def measure_stability(
    workload: Workload,
    policy: Policy,
    trie: PrefixTrie,
    *,
    orderings_count: int = 12,
    seed: int = 3,
) -> dict[str, Stability]:
    sequences = orderings(workload, orderings_count, seed)
    collected: dict[str, list[list[float]]] = {}
    for sequence in sequences:
        result = serve(workload, policy, sequence, trie=trie)
        for name, allocation in allocate_all(workload, policy, trie, result).items():
            collected.setdefault(name, []).append(
                [allocation.prefill_shares[tenant] for tenant in workload.tenants]
            )

    stability: dict[str, Stability] = {}
    for name, runs in collected.items():
        columns = list(zip(*runs, strict=True))
        spreads = []
        for values in columns:
            mean = sum(values) / len(values)
            spreads.append((max(values) - min(values)) / mean if mean else 0.0)
        worst = max(range(len(spreads)), key=lambda index: spreads[index])
        stability[name] = Stability(
            scheme=name,
            orderings=len(sequences),
            max_relative_spread=max(spreads),
            mean_relative_spread=sum(spreads) / len(spreads),
            worst_tenant=workload.tenants[worst],
        )
    return stability


def decide_verdict(
    scheme: str,
    allocations: dict[str, Allocation],
    stability: dict[str, Stability],
    actual_cost: float,
    policy: Policy,
) -> Verdict:
    """Not a bill beats unstable, because an unstable division of the wrong total
    is not a finding about ordering."""
    allocation = allocations[scheme]
    if not allocation.sums_to(actual_cost):
        return Verdict.NOT_AN_ATTRIBUTION
    if stability[scheme].max_relative_spread > policy.attribution.material_share_shift:
        return Verdict.ORDER_DEPENDENT
    return Verdict.SOUND


def audit(
    workload: Workload,
    policy: Policy,
    *,
    scheme: str | None = None,
    orderings_count: int = 12,
) -> AuditResult:
    name = scheme or policy.attribution.default_scheme
    trie = build_trie(workload.requests)
    result = serve(workload, policy, trie=trie)
    allocations = allocate_all(workload, policy, trie, result)
    stability = measure_stability(workload, policy, trie, orderings_count=orderings_count)
    actual = result.cost(policy)
    return AuditResult(
        verdict=decide_verdict(name, allocations, stability, actual, policy),
        scheme=name,
        allocations=allocations,
        stability=stability,
        result=result,
        trie_nodes=trie.distinct_prefix_tokens,
        shared_nodes=trie.shared_nodes(),
        actual_cost=actual,
        seed=workload.seed,
    )
