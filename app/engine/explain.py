"""Template-only explanations (FR 7.4 documented no-model fallback).

Every explanation is a fixed frame (what happened / why it matters / impact /
recommended action) filled from Finding fields ONLY. No model, no free text, no
figure that is not already on the record. Each rule's frame lives on its
RuleSpec (`explanation_template`: three paragraphs separated by a blank line,
plus `recommended_action`); a few alternate finding kinds have their frame here.

Wording rule: data is "consistent or inconsistent with" a requirement. Nothing
here may pass `copy_lint.find_restricted`.
"""

from __future__ import annotations

import calendar
from decimal import Decimal
from typing import Any

from engine.metrics import fmt_hours, fmt_money, fmt_pct, fmt_x, plural
from engine.results import Explanation
from engine.rules.base import Finding, RuleSpec

# Alternate frames, keyed (rule_id, computed["kind"]): (template, recommended_action).
_VARIANTS: dict[tuple[str, str], tuple[str, str]] = {
    ("L-05", "late_entries"): (
        "{late_entries} of {total_entries} time entries ({late_rate_pct}) were created more than "
        "{late_threshold_hours} hours after the end of their work date."
        "\n\n"
        "Time is expected to be recorded timely under the cited requirements ({authorities}). A high share of late entries is "
        "inconsistent with that expectation and raises the risk that hours were reconstructed rather than "
        "recorded as worked."
        "\n\n"
        "{late_entries} entries · {employees_text} · {exposure} (hours x loaded rate over the late entries)",
        "Review the late entries with the employees' supervisors, confirm the hours against supporting "
        "records, and remind the team of the customer's timekeeping policy.",
    ),
    ("L-02", "aggregate"): (
        "In {period_label}, timekeeping and payroll hours differ by {total_abs_gap} hours in total, "
        "{metric_value_pct} of the {total_paid_hours} hours paid, across {employees_text}."
        "\n\n"
        "Hours recorded on timesheets are expected to reconcile to hours paid. An aggregate difference this "
        "large is inconsistent with the labor distribution expected under the cited requirements ({authorities})."
        "\n\n"
        "{total_abs_gap} hours · {employees_text} · {exposure} (gap hours x loaded rate)",
        "Review the employee-pay-periods with gaps, correct the record that is wrong in timekeeping or "
        "payroll, and document the reason.",
    ),
}

_VARIANTS[("C-01", "conditional")] = (
    "For {period_label}, {unallowable_amount} sits in the {pool_label} pool in {account_count_text} whose "
    "allowability depends on the facts: {accounts_text}."
    "\n\n"
    "Whether these costs are allowable turns on how they were incurred under the cited principles ({authorities}). "
    "The amount is large enough to be worth confirming."
    "\n\n"
    "{exposure} of conditionally allowable cost in the {pool_label} pool",
    "Review the underlying transactions. Keep the allowable part in the pool and move any part that is not "
    "allowable out of it.",
)

_MONEY_KEYS = {
    "charged_rate", "approved_rate", "rate_difference", "loaded_rate",
    "payroll_total", "gl_total", "variance", "pool_amount", "base_amount", "unallowable_amount",
}
_FRACTION_KEYS = {
    "window_share", "baseline_share", "edit_rate", "undocumented_share", "last_two_days_share",
    "late_rate", "variance_ratio", "share_now", "baseline_share",
}


def _period_label(period: str) -> str:
    try:
        return f"{calendar.month_name[int(period[5:7])]} {period[:4]}"
    except (ValueError, IndexError):
        return period


def _plain(v: Decimal) -> str:
    return format(v.normalize(), "f")


def _fmt_value(key: str, v: Any) -> Any:
    if isinstance(v, Decimal):
        if key in _MONEY_KEYS or key.endswith("_usd"):
            return fmt_money(v)
        if key == "hours" or key.endswith("_hours") or key in {"abs_gap", "total_abs_gap"}:
            return fmt_hours(v)
        return _plain(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if v is None:
        return "n/a"
    return v


class _Strict(dict):
    def __missing__(self, key: str) -> str:
        raise KeyError(f"explanation template needs '{key}', which is not on the finding")


def _context(f: Finding, spec: RuleSpec) -> dict[str, Any]:
    c = f.computed
    ctx: dict[str, Any] = {}
    for k, v in c.items():
        if k == "entry_costs":
            continue
        ctx[k] = _fmt_value(k, v)
        if k in _FRACTION_KEYS and isinstance(v, Decimal):
            ctx[f"{k}_pct"] = fmt_pct(v, 1)

    n_emp = len(f.employees)
    n_con = len(f.contracts)
    ctx.update(
        {
            "period": f.period,
            "period_label": _period_label(f.period),
            "rule_id": f.rule_id,
            "authorities": "; ".join(f.authorities),
            "exposure": fmt_money(f.exposure_usd),
            "employees_n": n_emp,
            "employees_text": (f.employees[0] if n_emp == 1 else f"{n_emp} employees"),
            "contracts_text": (
                f.contracts[0] if n_con == 1 else ("no contract" if n_con == 0 else f"{n_con} contracts")
            ),
            "metric_value_pct": fmt_pct(f.metric_value, 2),
        }
    )
    if n_emp == 1 and "employee_id" not in ctx:
        ctx["employee_id"] = f.employees[0]

    # Rule-specific derived strings, all built from record fields.
    if f.rule_id == "L-01":
        rd = c.get("rate_difference")
        if rd is not None and c.get("charged_rate") is not None:
            ctx["rate_sentence"] = (
                f"The recorded category's ceiling rate is {fmt_money(c['charged_rate'])} and the crosswalked "
                f"category's is {fmt_money(c['approved_rate'])}."
            )
            ctx["rate_math"] = (
                f" ({fmt_money(c['charged_rate'])} - {fmt_money(c['approved_rate'])} = {fmt_money(rd)} per hour "
                f"x {fmt_hours(c['hours'])} hours)"
            )
        else:
            ctx["rate_sentence"] = "The ceiling rates for one or both categories are not available on the contract."
            ctx["rate_math"] = ""
    elif f.rule_id == "L-02" and c.get("kind") == "employee_gap":
        ctx["direction_text"] = (
            "timekeeping exceeds paid hours" if c.get("direction") == "time_exceeds_pay" else "paid hours exceed timekeeping"
        )
    elif f.rule_id == "L-06":
        ctx["undocumented_text"] = plural(int(c.get("undocumented_count", 0)), "undocumented edit")
    elif f.rule_id == "L-03":
        ctx["variance_pct"] = fmt_pct(c["variance_ratio"], 2)
    elif f.rule_id == "L-05" and c.get("kind") == "cluster":
        ctx["cluster_index_txt"] = fmt_x(c["cluster_index"])
        ctx["baseline_note"] = (
            "The baseline uses fewer than three prior periods, so this comparison has low baseline confidence."
            if c.get("baseline_confidence") == "low"
            else ""
        )
    elif f.rule_id == "DQ-01":
        ctx["id_list"] = ", ".join(c.get("unmatched_ids", ()))
        ctx["payroll_clause"] = " and payroll" if c.get("in_payroll") else ""
        ctx["unmatched_text"] = plural(int(c.get("unmatched_n", 0)), "employee ID")
    elif f.rule_id == "L-08":
        ctx["share_shift_pp"] = format(Decimal(c["share_shift_pp"]).quantize(Decimal("0.1")), "f")
    elif f.rule_id == "C-01":
        ctx["unallowable_share_pct"] = fmt_pct(c["unallowable_share"], 1)
    elif f.rule_id == "C-02":
        ctx["days_overdue"] = int(c["days_overdue"])
        ctx["deadline_months"] = int(c["deadline_months"])
    elif f.rule_id == "C-03":
        ctx["provisional_rate_pct"] = fmt_pct(c["provisional_rate"], 2)
        ctx["ceiling_rate_pct"] = fmt_pct(c["ceiling_rate"], 2)
        ctx["excess_pct"] = fmt_pct(c["excess"], 2)
        ctx["actual_rate_pct"] = fmt_pct(c["actual_rate"], 2)
    elif f.rule_id == "L-11":
        actual, prov, m10 = c["actual_rate"], c["provisional_rate"], c["m10"]
        under = c.get("direction") == "under_billed"
        n = int(c.get("periods_beyond_watch", 0))
        ctx["actual_rate_pct"] = fmt_pct(actual, 2)
        ctx["provisional_rate_pct"] = fmt_pct(prov, 2)
        ctx["abs_m10_pct"] = fmt_pct(abs(m10), 1)
        ctx["above_below"] = "above" if under else "below"
        ctx["direction_label"] = "under-billed" if under else "over-billed"
        ctx["direction_text"] = (
            "recovers less than the costs actually incurred (under-billed)"
            if under
            else "recovers more than the costs actually incurred (over-billed)"
        )
        ctx["rate_gap_text"] = f"{fmt_pct(abs(actual - prov), 2)} gap"
        if c.get("systemic"):
            ctx["streak_sentence"] = (
                f"The drift has exceeded the Watch threshold for {n} consecutive periods, ending with this one, "
                "which is a systemic pattern."
            )
        elif n > 1:
            ctx["streak_sentence"] = (
                f"The drift has exceeded the Watch threshold for {n} consecutive periods, ending with this one."
            )
        else:
            ctx["streak_sentence"] = "This is the first period in which the drift exceeds the Watch threshold."
        ctx["streak_short"] = f"{plural(n, 'period')} beyond Watch"
    return ctx


def explain(finding: Finding, spec: RuleSpec) -> Explanation:
    kind = finding.computed.get("kind", "primary")
    template, action = _VARIANTS.get(
        (finding.rule_id, kind), (spec.explanation_template, spec.recommended_action)
    )
    parts = template.split("\n\n")
    if len(parts) != 3:
        raise ValueError(f"explanation template for {spec.id} must have three paragraphs, has {len(parts)}")
    ctx = _Strict(_context(finding, spec))
    what, why, impact = (part.format_map(ctx).strip() for part in parts)
    return Explanation(
        what_happened=what,
        why_it_matters=why,
        impact=impact,
        recommended_action=action,
    )
