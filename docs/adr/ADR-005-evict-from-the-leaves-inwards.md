# ADR-005: Eviction is from the leaves inwards, and the oracle policy is kept

Status: accepted

## Context

Once the cache has a finite capacity, something has to be chosen for eviction, and
two decisions follow that shape every capacity figure in this repository.

The first is which nodes are eligible. A prefix cache cannot hold a child without
its parent: the child's key and value blocks were computed from the parent's
state, so an interior node whose descendants are resident is not independently
evictable. A simulator that evicted an interior node and kept its subtree would be
modelling something no cache does, and it would flatter the hit rate by keeping
deep prefixes alive for free.

The second is which policy to measure. LRU is what production prefix caches ship,
and an LRU number alone cannot separate "the cache is too small" from "the
eviction policy is wrong". Those are different complaints with different fixes and
very different costs.

## Decision

Eviction is restricted to resident leaves. A node with resident children is
skipped and becomes a candidate once its children have gone.

Two policies are implemented. `lru` evicts the least recently used resident leaf,
using a heap with lazy invalidation: entries are never removed when a node is
touched again, and a stale entry is recognised because its recorded clock is not
the node's current one and is discarded on the way past. `oracle` evicts the
resident leaf needed furthest in the future, by direct scan, which is not
implementable in production and is not meant to be.

The oracle is kept in the shipped tool rather than confined to an experiment,
behind `--oracle` on the `cache` command, because the gap between the two is the
number a capacity planning conversation actually needs.

## Consequences

The separation paid for itself immediately. At the shipped capacity **an oracle
needs only 24,613** prefill tokens, which is the unbounded optimum exactly: an
optimal policy at a third of the working set never evicts anything that will be
needed again. LRU at the same capacity is **spending 0.0532 more than** it needs
to. So on this workload the capacity is sufficient and the policy is the cost,
which is the opposite of the conclusion a single prefill number invites.

Across the sweep the policy costs as much as **as much as 0.2995 more** than
optimal at the worst capacity, which is where a cache is small enough for LRU's
recency heuristic to keep evicting prefixes that are about to be reused.

The direct scan makes the oracle the slow path, which is stated rather than
hidden: its priority for every node changes at every position, so a heap would be
almost entirely stale entries. It is only ever run to bound the fast policy, and
the capacity sweep with both policies still finishes in about a minute.

The leaf restriction has one visible cost. The LRU victim search can walk past
several interior nodes before finding a leaf, and those are re-pushed rather than
dropped. The lazy heap keeps that near constant amortised, which is why the sweep
finishes at all.

## Alternatives rejected

**Evict any node and let the subtree dangle.** Simpler and wrong. The reported hit
rate would include hits on prefixes whose parents were gone, which no cache can
serve.

**Evict a whole path at once.** Closer to how some implementations release a
sequence, and it makes the eviction count depend on path length rather than on
tokens, which would have made the capacity axis mean something different from the
one every other number here is measured against.

**Least frequently used instead of LRU.** A reasonable alternative that would
measure a different heuristic against the same oracle. Left out because two
implementable policies invite a comparison between them, and the comparison worth
publishing is between what a real policy does and what the best possible policy
would have done.
