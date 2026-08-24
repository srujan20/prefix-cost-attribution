"""Text and HTML reports, with the total each scheme divides printed beside it.

The column that matters is not the per tenant share, it is whether the shares sum
to what the server spent. A scheme that divides the wrong total can produce a
beautiful, defensible, order independent allocation of a number nobody owes, and
printing the shares without the total is how that goes unnoticed.

The HTML is hand written for the same reason as in the sibling repositories: the
README screenshots are captured from this output by a headless browser that frames
on named headings and asserts a contrast ratio on the verdict badge, so the
heading ids and the badge colours are part of the contract rather than styling.
"""

from __future__ import annotations

import html
import json

from .audit import AuditResult, Verdict
from .config import Policy

BADGE_COLOURS = {
    Verdict.SOUND: ("#0b3d2c", "#d6f3e6"),
    Verdict.ORDER_DEPENDENT: ("#5c1111", "#fbdcdc"),
    Verdict.NOT_AN_ATTRIBUTION: ("#4a3708", "#fdf0d3"),
}

VERDICT_SENTENCE = {
    Verdict.SOUND: (
        "The shipped scheme sums to what the server spent, and gives the same shares "
        "under every ordering tested."
    ),
    Verdict.ORDER_DEPENDENT: (
        "The shipped scheme sums to what the server spent, and a tenant's share moves "
        "between orderings of the same requests by more than the configured threshold."
    ),
    Verdict.NOT_AN_ATTRIBUTION: (
        "The shipped scheme does not sum to what the server spent, so whatever it is "
        "dividing, it is not the bill. Ordering does not arise."
    ),
}


def text_report(result: AuditResult, policy: Policy) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"verdict: {result.verdict.value}")
    add(f"exit code: {result.verdict.exit_code}, scheme {result.scheme}, seed {result.seed}")
    add("")
    add(VERDICT_SENTENCE[result.verdict])
    add("")
    # The threshold is printed with the verdict rather than left in the config,
    # because "order-dependent" is a claim about a number crossing a line and a
    # reader who cannot see the line cannot check the claim.
    add(
        f"materiality: a share is called moved above {policy.attribution.material_share_shift:.4f} "
        f"relative spread, from {policy.source}"
    )
    add("")

    add("what the server actually spent")
    add(f"  prompt tokens sent      : {result.result.prompt_tokens}")
    add(f"  prefill tokens processed: {result.result.prefill_tokens}")
    add(f"  decode tokens generated : {result.result.decode_tokens}")
    add(f"  prefix cache hit share  : {result.result.hit_share:.4f}")
    add(f"  distinct prefix tokens  : {result.trie_nodes} (of which {result.shared_nodes} shared)")
    add(f"  total cost              : {result.actual_cost:,.0f}")
    add("")

    add("what each scheme says, and whether it is a division of that total")
    add(f"  {'scheme':<14}{'total':>14}{'sums to bill':>14}{'order stable':>14}{'spread':>10}")
    for name, allocation in result.allocations.items():
        stability = result.stability[name]
        add(
            f"  {name:<14}{allocation.total:>14,.0f}"
            f"{allocation.sums_to(result.actual_cost)!s:>14}"
            f"{stability.exactly_stable!s:>14}"
            f"{stability.max_relative_spread:>10.4f}"
        )
    add("")
    add("the row this report exists for")
    add(
        f"  a per request token count charges {result.over_attribution:.4f} times what the "
        "server spent"
    )
    add("  the surplus is the cached share, which the cache created and nobody billed anyone for")
    return "\n".join(lines) + "\n"


def html_report(result: AuditResult, policy: Policy) -> str:
    foreground, background = BADGE_COLOURS[result.verdict]
    rows = []
    for name, allocation in result.allocations.items():
        stability = result.stability[name]
        sums = allocation.sums_to(result.actual_cost)
        rows.append(
            f'<tr><td class="name">{html.escape(name)}</td>'
            f"<td class='num'>{allocation.total:,.0f}</td>"
            f'<td class="{"good" if sums else "bad"}">{"yes" if sums else "no"}</td>'
            f'<td class="{"good" if stability.exactly_stable else "bad"}">'
            f"{'exactly' if stability.exactly_stable else 'no'}</td>"
            f"<td class='num'>{stability.max_relative_spread:.4f}</td></tr>"
        )

    shares = result.allocations[result.scheme].prefill_shares
    reference = result.allocations["shapley"].prefill_shares
    tenant_rows = "".join(
        f"<tr><td class='name'>tenant {tenant}</td>"
        f"<td class='num'>{shares[tenant]:,.0f}</td>"
        f"<td class='num'>{reference[tenant]:,.0f}</td>"
        f"<td class='num'>{(shares[tenant] - reference[tenant]) / reference[tenant]:+.4f}</td></tr>"
        for tenant in sorted(shares)[:12]
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>prefixcost audit, seed {result.seed}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ background:#ffffff; color:#14171a; margin:0; padding:32px 40px;
         font:15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  h2 {{ font-size:15px; margin:28px 0 8px; color:#3d4348; }}
  .badge {{ display:inline-block; padding:6px 14px; border-radius:6px; font-weight:650;
            color:{foreground}; background:{background}; }}
  .lead {{ max-width:62em; color:#3d4348; margin:10px 0 0; }}
  table {{ border-collapse:collapse; margin-top:6px; }}
  th, td {{ padding:6px 16px 6px 0; text-align:left; border-bottom:1px solid #eceff1; }}
  th {{ color:#5b636b; font-weight:600; font-size:13px; }}
  td.num {{ font-variant-numeric:tabular-nums;
            font-family:ui-monospace, Menlo, Consolas, monospace; }}
  td.name {{ font-family:ui-monospace, Menlo, Consolas, monospace; }}
  .good {{ color:#1c5c3a; font-weight:600; }}
  .bad {{ color:#8c1d1d; font-weight:650; }}
  .note {{ max-width:62em; color:#5b636b; margin-top:10px; }}
</style></head><body>
<h1>Cost attribution under a prefix cache</h1>
<p><span class="badge">{html.escape(result.verdict.value)}</span>
   &nbsp;exit code {result.verdict.exit_code}, scheme {html.escape(result.scheme)},
   seed {result.seed}</p>
<p class="lead">{html.escape(VERDICT_SENTENCE[result.verdict])}</p>

<h2 id="spend">What the server actually spent</h2>
<table><tbody>
<tr><td>prompt tokens sent</td><td class="num">{result.result.prompt_tokens:,}</td></tr>
<tr><td>prefill tokens processed</td><td class="num">{result.result.prefill_tokens:,}</td></tr>
<tr><td>decode tokens generated</td><td class="num">{result.result.decode_tokens:,}</td></tr>
<tr><td>prefix cache hit share</td><td class="num">{result.result.hit_share:.4f}</td></tr>
<tr><td>distinct prefix tokens</td><td class="num">{result.trie_nodes:,}</td></tr>
<tr><td>of those, shared between tenants</td><td class="num">{result.shared_nodes:,}</td></tr>
<tr><td>total cost</td><td class="num">{result.actual_cost:,.0f}</td></tr>
</tbody></table>

<h2 id="schemes">What each scheme says, and whether it divides that total</h2>
<table><thead><tr><th>scheme</th><th>total</th><th>sums to the bill</th>
<th>same under every ordering</th><th>widest tenant spread</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<p class="note">A scheme that does not sum to the bill can still be beautiful,
defensible and order independent. It is then a division of a number nobody owes,
which is why this column is printed before the shares rather than after them.
A spread above {policy.attribution.material_share_shift:.4f} is what this run calls
materially moved, from {html.escape(policy.source)}.</p>

<h2 id="tenants">The shipped scheme against the fair split, per tenant</h2>
<table><thead><tr><th>tenant</th><th>shipped, prefill</th><th>Shapley, prefill</th>
<th>difference</th></tr></thead><tbody>{tenant_rows}</tbody></table>
<p class="note">Prefill only. Decode is never shared, every scheme agrees about it,
and including it would divide the disputed quantity by a much larger number and
report that the dispute is small.</p>
</body></html>
"""


def json_report(result: AuditResult, policy: Policy) -> str:
    payload = result.as_dict()
    payload["policy_source"] = policy.source
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
