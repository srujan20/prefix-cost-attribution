# ADR-001: The prefix cache is the trie, not a map of whole prompts

Status: accepted

## Context

Every figure in this repository is a share of prefill work, so the simulator's
model of what a prefix cache holds decides every number downstream of it.

The first implementation was a dictionary keyed by the complete prompt. A request
whose exact prompt had been seen before paid nothing; anything else paid for all
of its tokens. It is the obvious first draft, it is easy to reason about, and it
is a model of a *request* cache rather than a prefix cache.

The difference is not a detail. A prefix cache stores key and value blocks per
token position, so any prefix that has ever been computed is reusable, including
one that no request ever sent on its own. Turn two of a conversation computes a
prefix that turn three reuses, and no request in the workload is ever equal to
that prefix. Two tenants sharing a system prompt share its blocks even though
their full prompts differ from the first token after it.

## Decision

Residency is a flag on a node of the prefix trie. Serving a request walks its
token path, finds the deepest resident node, pays for the remainder, and marks the
whole path resident. The cache and the trie are the same structure.

This makes the unbounded case an identity rather than an approximation. With a
capacity at or above the node count, total prefill equals the node count exactly,
in any arrival order, because each distinct prefix token is computed once. The
suite asserts that on the nose rather than within a tolerance.

## Consequences

The correction moved the headline. Under an unbounded cache the whole prompt
version reported 28,504 prefill tokens where the trie reports **24,613 distinct
prefix tokens**, an understatement of reuse by about a quarter, and every
attribution built on it was dividing a total that was too large.

It also created the second anchor. With no cache at all the prefill is the
**337,098 prompt tokens** the workload sent, exactly, so the two ends of the
capacity sweep are both integers computable without simulating anything. Both are
checked on eight workloads and, for the unbounded case, **under 96 permutations**
of the arrival order, because a quantity that is a property of the request set
cannot depend on the order they arrived in and the fact that it cannot is what
makes it usable as an anchor.

The cost is that the simulator carries a trie for every workload, which is memory
proportional to the distinct prefix tokens rather than to the requests. On this
corpus that is a structure of a few tens of thousands of nodes and the whole
capacity sweep runs in a minute.

## Alternatives rejected

**Keep the whole prompt map and call it a request cache.** Honest, and it would
have made the repository about a different and much less interesting phenomenon.
Request level caching is rare in production LLM serving and prefix caching is
standard, so the interesting attribution question would not have arisen.

**Model attention block granularity, with a block size of sixteen or thirty two
tokens.** Closer to a real implementation, since real caches evict blocks rather
than tokens. It would change the numbers by rounding every prefix down to a block
boundary and would change no conclusion, while adding a parameter that a reader
would reasonably ask me to justify and that this corpus cannot inform. Left out,
and named here so the omission is a decision rather than an oversight.

**Track residency by token sequence rather than by node identity.** Equivalent and
slower, since it hashes a tuple of up to several hundred tokens per lookup where
the trie walk is already producing the node.
