# ADR-004: `not-an-attribution` outranks `order-dependent`

Status: accepted

## Context

The tool audits one scheme and returns one verdict, so when a scheme has more
than one thing wrong with it, the ordering between those faults is a decision.

Two faults are possible. A scheme's shares can fail to sum to what the server
spent, in which case it is dividing some other number. Or they can sum correctly
and move between arrival orders of the same requests, in which case it is a bill
that partly records scheduling.

A per request token count, which is what almost every provider invoices, has the
first fault permanently once a prefix cache is switched on. It **collects 1.8378
times** what the server spent on the shipped configuration, and it is perfectly
stable across orderings, because it never looks at the cache at all.

So a naive ordering that reported stability first would give the most common
billing scheme in the industry a clean bill of health on the stability axis and
never mention that it is not a division of anything.

## Decision

Three verdicts, checked in this order.

`not-an-attribution`, exit 2. The shares do not sum to what the server spent. The
question of ordering does not arise, because whatever this divides, it is not the
bill.

`order-dependent`, exit 1. The shares sum to the spend, and some tenant's share
moves between orderings by more than the configured materiality.

`attribution-sound`, exit 0. Neither, and only then.

The rule is that a scheme has to be a division of the right total before its other
properties are worth discussing. An unstable division of the wrong number is not a
finding about instability.

## Consequences

The scheme this tool would recommend does not always earn exit 0, and that is the
point rather than an embarrassment. At the shipped **capacity of 8,000 tokens** the
cache recomputes prefixes it had already computed, so the server spends more than
the trie's node count, and the **fair split collects 0.9964** of that: it is
**short of the bill by 0.0036** and returns exit 2. The provider absorbs the
difference and no line item anywhere records it.

Raise the capacity and the same scheme on the same workload becomes sound. The
fair split is **a bill again from 20,000**, where LRU is **still evicting 4,613
nodes**, which is the more interesting half: what breaks efficiency is recomputing
an evicted prefix, not evicting one. Evicting a node nothing will ask for again
costs nothing and changes no bill.

Both sides of that boundary are pinned by tests, because a boundary asserted in
one direction only is an assumption wearing a test's clothes.

## Alternatives rejected

**Report both faults and exit 1.** A single exit code is the entire interface for
a CI step, and collapsing two different diagnoses into one number means the
caller cannot act differently on them. The composite action lets exit 2 warn
rather than block for exactly this reason: a team that has just measured its own
over-collection needs to see the verdict for a while before it gates a merge on
it.

**Let the caller choose the ordering.** A configurable severity ordering is a
setting that encodes an argument, and the argument has a right answer.

**Treat a small shortfall as a pass.** The tolerance for "sums to the bill" is one
part in a million, and widening it to cover the measured shortfall would set the
threshold from the answer. The shortfall is reported as a magnitude beside the
boolean instead, so a reader can see it is a third of a percent rather than half.
