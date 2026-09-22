# Sadhik AI: MVP demo

A runnable demo of the V1 design in `../docs/hld/system-design.md`. Synthetic government-contractor data goes
in, deterministic reconciliation findings come out, and you review them in a browser. Two compliance domains
run on the same engine: **Labor** and **DCAA cost accounting**.

**This is a demo over synthetic data, not the V1 product.** The project docs say engine infrastructure waits on
concierge runs against real data. This exists to make the design testable and showable. No real customer data,
no auth, no multi-tenancy, no LLM calls, no MCP server.

**Step-by-step instructions to run it: [`../docs/how-to-run.md`](../docs/how-to-run.md).** The short version:

```bash
cd app
uv venv --python 3.13 && uv pip install -r requirements.txt   # once
.venv/bin/python -m data.generate                              # writes data/out/ (deterministic)
SADHIK_DATA_DIR=data/out .venv/bin/uvicorn api.main:app --port 8000     # terminal 1
cd web && npm install && npm run dev                                    # terminal 2, http://localhost:5173
```

Press **Run now** (tier: Close). You get **14 open findings, 8 high, $102,491.51 total exposure, coverage 11 of 11**:
Labor 8 findings / $41,298.93 and DCAA cost accounting 6 findings / $61,192.58.

## What it demonstrates

| Design | Where |
|---|---|
| Deterministic rules over a per-run working view (§4, §5) | `engine/rules/`, `engine/ingest.py` |
| Floors, defaults and customer config validated by strictness `direction` (§5.3) | `engine/config.py`, `config/floors.yaml`, `config/meridian-rules.yaml` |
| Compliance domains, enabled per customer; per-domain and overall status (§5.7) | `engine/rollup.py`, `engine/runner.py` |
| Run manifest and replay (§5.5) | `engine/manifest.py` |
| Tiered runs; a partial run never reports a pass (§7) | `engine/runner.py`, `engine/rules/base.py` |
| M13 exposure de-duplication, four-layer rollup (§6.1) | `engine/metrics.py`, `engine/rollup.py` |
| Findings lifecycle and compliance memory (§8.3) | `api/service.py` |
| Explanation before evidence; per-tier and per-domain status (§7, §8.2) | `web/src/pages/` |
| Worked examples with the real figures (Appendix A labor, Appendix B DCAA) | `../docs/hld/system-design.md` §15, §16 |
| Confirmed reference tables; rules that never pass on missing data | `engine/ingest.py`, `engine/rules/c01.py`, `c02.py`, `c03.py` |

## The rules

| Rule | Domain | Tier | Checks |
|---|---|---|---|
| L-01 | Labor | pay period | Labor category charged vs. HR title vs. contract-approved categories |
| L-02 | Labor | pay period | Timekeeping hours vs. payroll hours |
| L-03 | Labor | close | Payroll labor dollars vs. GL labor accounts |
| L-05 | Labor | fast | Late entries and Friday-afternoon entry clustering |
| L-06 | Labor | fast | Post-submission edits |
| L-09 | Labor | fast | Charges outside the period of performance |
| **L-08** | DCAA cost accounting | fast | Direct/indirect classification against the employee's own history |
| **L-11** | DCAA cost accounting | close | Indirect pool/base rates against provisional billing rates |
| **C-01** | DCAA cost accounting | close | Unallowable cost (FAR 31.205 categories) sitting in an indirect pool |
| **C-02** | DCAA cost accounting | close | Incurred cost submission not made by six months after fiscal year end |
| **C-03** | DCAA cost accounting | close | A provisional billing rate above its contractual ceiling rate |
| DQ-01 | Labor (data-quality gate) | pay period | Employee IDs missing from the HRIS roster |

## Tests

```bash
.venv/bin/python -m pytest tests     # 381 tests
cd web && npm test                   # 75 tests
```

The gate that matters is `tests/test_recall.py`: the engine, reading only the CSVs, must find exactly what the
generator injected (`data/out/ground_truth.json`): same fingerprints, exposure to the cent, severity, and the
de-duplicated total. It also runs mutation checks (change the data, confirm the finding moves by exactly the
right amount), determinism, row-order invariance, replay, tiering, and the domain rules. `data/verify_ground_truth.py`
re-derives the answer key from the CSVs with no engine code, so the key and the engine are computed independently.

## Known limitations

- **Contract-scoped config overrides are validated but not applied.** No rule reads them yet; the validator
  warns. Rule-wide overrides work.
- **8 of the design's 13 catalog rules exist** (L-04, L-07, L-10, L-12, L-13 are missing), plus three cost-accounting
  rules (C-01, C-02, C-03) that the design's catalog does not have.
- **Which accounts are unallowable is a confirmed table, not something the engine decides**, and the seeded table is
  synthetic. The FAR 31.205 citations, the conditional-cost citation and the rate-ceiling clause are marked "to verify".
- **Every rule is `review_status: unreviewed`.** No GovCon CPA has reviewed any citation or floor, and CAS 402
  and CAS 418 are marked "to verify". Floors exist only for the two zero-tolerance checks (L-01, L-09). Do not
  show this to a customer as reviewed regulatory guidance.
- **Prior-period history is seeded, not stored.** The baselines for L-05, L-06, L-08 and L-11 come from
  `metric_history.json` and `classification_history.json`. Real multi-period history would come from stored runs.
- **The pool and base definitions and the provisional rates are synthetic.** A real customer's would need a
  CPA to confirm them.
- Mappings and contract terms are pre-confirmed static files (the post-confirmation state); there is no upload
  UI, LLM mapping or PDF extraction. Disposition attachments are not supported.
- `data/out/` follows the generator's answer key, which differs from design doc section 15 in places (see
  `deviations_from_design_doc` in `ground_truth.json`).
