"""Establish the two quantities in this repository that are exact, and check them.

Almost every number in a cost model is an estimate of something. Two here are
not, and everything else is calibrated against them.

The first: with no cache at all, the prefill the server does is the workload's
prompt token count, exactly. Nothing is reused, so the tokens processed and the
tokens sent are the same integer.

The second: with a cache large enough never to evict, the prefill is the number
of nodes in the prefix trie, exactly. Each distinct prefix token is computed once
and reused thereafter, whatever order the requests arrive in.

Neither is approached, converged to, or estimated. They are integers and this
experiment asserts them on the nose, because a change that quietly breaks prefix
reuse would otherwise move a rate by a percent that nobody would query.

The experiment also reports what the corpus is made of, since the size of the
gap between those two anchors is the whole reason a prefix cache exists.

Usage:
    python experiments/exp01_the_two_exact_anchors.py [--seeds N]
"""

from __future__ import annotations

import argparse

from _shared import banner, policy, rate_dict, summary, workload_and_trie, write

from prefixcost.serving import serve
from prefixcost.workload import orderings

NAME = "exp01-the-two-exact-anchors"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=8)
    args = parser.parse_args(argv)

    settings = policy()
    banner(f"checking both anchors on {args.seeds} workloads, and each under 12 orderings")

    rows = []
    uncached_exact = 0
    unbounded_exact = 0
    permutation_trials = 0
    permutation_exact = 0

    for index in range(args.seeds):
        seed = 11 + index
        workload, trie = workload_and_trie(seed)

        uncached = serve(workload, settings, capacity_tokens=0, trie=trie)
        unbounded = serve(workload, settings, capacity_tokens=trie.nodes, trie=trie)

        # The assertions are the experiment. If either fails the repository is
        # measuring something other than a prefix cache and no downstream figure
        # means anything.
        assert uncached.prefill_tokens == workload.prompt_tokens
        assert unbounded.prefill_tokens == trie.distinct_prefix_tokens
        assert unbounded.evictions == 0
        uncached_exact += 1
        unbounded_exact += 1

        # And under permutation. The node count is a property of the set of
        # requests, so it cannot depend on their arrival order, and the fact
        # that it cannot is what makes it usable as an anchor.
        for sequence in orderings(workload, 12, seed):
            permuted = serve(workload, settings, sequence, capacity_tokens=trie.nodes, trie=trie)
            permutation_trials += 1
            if permuted.prefill_tokens == trie.distinct_prefix_tokens:
                permutation_exact += 1

        rows.append(
            {
                "seed": seed,
                "requests": len(workload.requests),
                "tenants": len(workload.tenants),
                "vocabulary_size": workload.vocabulary.size,
                "prompt_tokens": workload.prompt_tokens,
                "output_tokens": workload.output_tokens,
                "distinct_prefix_tokens": trie.distinct_prefix_tokens,
                "shared_nodes": trie.shared_nodes(),
                "uncached_prefill": uncached.prefill_tokens,
                "unbounded_prefill": unbounded.prefill_tokens,
                "reuse_share": 1.0 - trie.nodes / workload.prompt_tokens,
            }
        )

    headline = rows[0]
    payload = {
        "seeds": args.seeds,
        "rows": rows,
        "headline_seed": headline["seed"],
        "prompt_tokens": headline["prompt_tokens"],
        "distinct_prefix_tokens": headline["distinct_prefix_tokens"],
        "shared_nodes": headline["shared_nodes"],
        "shared_node_share": headline["shared_nodes"] / headline["distinct_prefix_tokens"],
        "vocabulary_size": headline["vocabulary_size"],
        "requests": headline["requests"],
        "tenants": headline["tenants"],
        "reuse_share": summary(row["reuse_share"] for row in rows),
        "anchor_holds_uncached": rate_dict(uncached_exact, args.seeds),
        "anchor_holds_unbounded": rate_dict(unbounded_exact, args.seeds),
        "anchor_holds_under_permutation": rate_dict(permutation_exact, permutation_trials),
        "nodes_by_tenant_count": workload_and_trie(headline["seed"])[1].nodes_by_tenant_count(),
    }
    write(NAME, payload)

    print()
    print(f"  {'seed':>6}{'prompt tokens':>16}{'distinct prefix':>18}{'reuse':>9}")
    for row in rows:
        print(
            f"  {row['seed']:>6}{row['prompt_tokens']:>16,}"
            f"{row['distinct_prefix_tokens']:>18,}{row['reuse_share']:>9.4f}"
        )
    print()
    print(f"  no cache, prefill == prompt tokens exactly: {uncached_exact} of {args.seeds}")
    print(f"  unbounded, prefill == trie nodes exactly  : {unbounded_exact} of {args.seeds}")
    print(
        f"  and under permutation                     : {permutation_exact} of {permutation_trials}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
