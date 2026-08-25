# ADR-002: Compute the Shapley value exactly, in one pass, rather than sample it

Status: accepted

## Context

The fair split of a shared cost has a standard answer. The Shapley value is the
average, over every order in which the players could have arrived, of what each
player added to the cost when it arrived. It is the unique allocation that is
efficient, symmetric, gives a dummy nothing, and is additive, and those four
properties are exactly the ones a customer asking "why is my bill this number"
is really asking about.

Its reputation is that it is not computable. The average is over `n!` orders, so
the received advice is to sample a few hundred permutations and publish the mean.

That advice is right in general and wrong here, because the cost game in this
repository is played on a tree. A coalition of tenants pays for the trie nodes
their requests cover. For that game a player's Shapley value is the sum, over the
nodes on its own paths, of that node's cost divided by the number of players using
the node. The exponential sum collapses to one pass over the structure.

## Decision

`attribution.shapley` walks the trie once. Each node costs one prefill token and
is split equally among the tenants whose requests pass through it. Nothing is
sampled and no permutation is ever constructed.

The identity is checked against the definition rather than against a rerun of
itself. `tests/test_shapley_against_brute_force.py` builds a small trie by hand,
averages marginal contributions over all 120 permutations of five players, and
asserts the two agree. It duplicates the formula rather than importing it, so a
later refactor cannot move both sides of the comparison at once.

## Consequences

A sampled Shapley value carries a Monte Carlo error into an invoice, and the
benchmark measures what that costs. The sampled implementation it is measured
against is written properly rather than strawmanned: each permutation costs one
pass over the covered node set, which is the best that approach can do.

At the largest size the **exact pass takes 1.8 ms** at the smallest workload and
**9.2 ms at the largest**, where the **sampled pass takes 2411.3 ms**, which is
**261.6 times the exact pass**. The exact pass has **a linearity ratio of 1.01**
in the node count, so the collapse from exponential to linear is visible in the
timings rather than only in the argument.

Accuracy matters more than speed here. The smallest sample that keeps every
tenant inside the configured materiality threshold **takes 200 permutations**, and
at that count the worst tenant is **still wrong by 0.0381**. At ten permutations
the worst tenant is **off by 0.1897 at ten**. So the sampling approach is not
merely slower, it hands a customer a bill with an error bar on it, and buying a
smaller error bar costs linearly more compute forever.

## Alternatives rejected

**Sample permutations, with enough of them.** There is no count that reaches zero
error, so the question is only which non zero error is acceptable on an invoice,
and that is a conversation nobody wants to have with a customer.

**A different fair division: the nucleolus, or the Aumann-Shapley price.** Both
are defensible and neither has the tree shortcut, so both would have to be
sampled or solved, which is the problem this decision avoids. The Shapley value
also has the advantage of an explanation a non specialist accepts: everyone who
used a prefix pays an equal share of it.

**Proportional to each tenant's own token count.** Shipped as `proportional`, and
kept in the tool precisely because it is what a finance team reaches for. It
charges a tenant for shared prefixes it never used, in proportion to usage it
incurred somewhere else, and the per tenant table shows where that lands.
