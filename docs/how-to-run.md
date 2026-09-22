# How to run the Sadhik AI MVP

The app is a Python API (FastAPI + SQLite) with a React UI. Everything runs locally against synthetic data.
Commands assume you start in the repository root (`sadhik-ai/`).

## 1. What you need

| | Version | Check |
|---|---|---|
| Python | 3.13 | `python3 --version` |
| uv | any recent | `uv --version` |
| Node.js and npm | Node 20.19+ or 22.12+ (built and tested with 24) | `node --version` |

## 2. One-time setup

```bash
cd app

# Python environment and dependencies
uv venv --python 3.13
uv pip install -r requirements.txt

# Frontend dependencies
cd web && npm install && cd ..

# Generate the synthetic Meridian Systems data (deterministic: same bytes every time)
.venv/bin/python -m data.generate
```

`data.generate` writes 17 files into `app/data/out/`: the timekeeping, payroll, GL, HRIS and rate exports, the contract
terms, the confirmed reference tables (charge codes, account categories, account allowability, the filing schedule and
others), and `ground_truth.json` (the answer key the tests grade against).

## 3. Run it

Two terminals, both from `app/`.

**Terminal 1: the API (port 8000)**

```bash
SADHIK_DATA_DIR=data/out .venv/bin/uvicorn api.main:app --port 8000
```

**Terminal 2: the UI (port 5173)**

```bash
cd web && npm run dev
```

Open **http://localhost:5173**. The API's own interactive docs are at http://localhost:8000/docs.

The API does not auto-reload. **Restart it after regenerating the data or changing engine code.**

## 4. A five-minute tour

The database starts empty, so the first screen says "Incomplete data" until you run a check.

1. **Status.** Choose tier **Close** and press **Run now**. You get **14 open findings** (8 high, 5 medium, 1 low),
   **$102,491.51** total exposure and coverage **11 of 11**. Two domain tiles show their own numbers: Labor
   (8 findings, 6 of 6) and DCAA cost accounting (6 findings, 5 of 5).
2. **Open a domain.** Click the **DCAA cost accounting** tile. The queue is filtered to its six findings:
   - **C-01** $23,550.00 of unallowable cost (entertainment, lobbying, fines) in the G&A pool, and $1,850.00 (alcoholic
     beverages) in the overhead pool;
   - **C-02** the FY2025 incurred cost submission, due 2026-06-30 and not recorded (62 days overdue, no dollar exposure);
   - **C-03** the overhead provisional rate (18.00%) above its contractual ceiling (17.50%), $8,663.24 billed above the cap;
   - **L-08** E-0143's 40.0 hours moved from a T&M contract to overhead;
   - **L-11** the G&A rate drifting to 8.0% above its provisional rate.
3. **Read a finding.** Open the G&A rate finding, or the unallowable-cost one. The order is deliberate: what happened, why
   it matters (with the authorities cited), impact, recommended action, and only then the evidence: the GL lines behind
   it with their `file : row` and hashes. The unallowable-cost finding lists exactly the three accounts with their
   FAR 31.205 citations. A fourth line, gifts at $4,100.00, is "conditional" and below materiality, so it is logged and
   not raised.
4. **Disposition it.** Press *in review*, then *legitimate exception*. That needs a reason code **and** a note.
   Back on Status, total exposure has dropped by exactly **$23,477.34** (to $79,014.17) and the DCAA tile shows 5 open findings.
5. **Try the config.** Go to **Rules & config**. Change `late_threshold_hours` to `200` and press *Validate*: it is
   refused with a line number, and never clamped to a legal value. Try `floor: 5` under any rule, or
   `enabled: false` on L-09; each is refused for its own reason. A stricter value is accepted.
6. **Turn a domain off.** In the config, under `domains:`, delete the line `- dcaa_cost_accounting` so only
   `- labor` remains. Press *Save*, then *Run now*. The DCAA tile reads **Not enabled**, and total exposure is
   **$41,298.93** (labor only). Put the line back and save to turn it on again.
7. **Tiers.** Run the **Fast** tier. Coverage stays 11 of 11, because earlier results are carried forward; the tier
   table shows what ran when.
8. **Replay.** Open **Runs**, pick a run, press *Replay*. It re-executes from the pinned inputs and reports
   `Identical: true`.

## 5. Try to break it

The point of the demo is that missing or altered data is never presented as a clean result.

- **Remove the rate data.** Edit `app/data/out/sources.json`: move `rate_data` from `sources` into the `absent`
  list. Run again. L-11, C-02 and C-03 all become **Not evaluated** ("Provisional billing rate data was not uploaded"),
  the DCAA tile reads **Incomplete data** (2 of 5), and so does the overall label, even though labor is fine. Undo the
  edit afterwards (`python -m data.generate` restores it).
- **Remove just one input.** Delete `app/data/out/account_allowability.csv` and run again. Only C-01 becomes **Not
  evaluated**. Overall coverage is still 10 of 11 (0.91, above the 0.85 floor), yet the label reads **Incomplete data**,
  because the DCAA domain is at 4 of 5 (0.80). A healthy total cannot hide a thin domain. Restore it with
  `python -m data.generate`.
- **Change an input.** Edit any data file (add a blank line to `meridian_time_2026-08.csv`, say), then press *Replay*
  on an older run. It reports **Identical: false** and names the file that "no longer matches the hash recorded in
  the manifest", rather than quietly producing different findings.

## 6. Reset

The state lives in one SQLite file. Stop the API, delete it, start again:

```bash
rm api/sadhik.db            # or the path you set in SADHIK_DB
```

## 7. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SADHIK_DATA_DIR` | `data/out` | Folder holding the source exports |
| `SADHIK_DB` | `api/sadhik.db` | SQLite file for runs, findings, dispositions and config versions |
| `API_TARGET` (UI dev server) | `http://localhost:8000` | Where Vite proxies `/api` |

To run on other ports (for example if 8000 or 5173 is taken):

```bash
SADHIK_DATA_DIR=data/out .venv/bin/uvicorn api.main:app --port 8010
cd web && API_TARGET=http://localhost:8010 npm run dev -- --port 5180
```

## 8. UI without the backend

`web/mock-server.mjs` serves canned responses from `contracts/sample_payloads/`, useful for working on the UI alone:

```bash
cd web && npm run mock          # port 8000; set PORT=8010 to change it
```

## 9. Tests

```bash
.venv/bin/python -m pytest tests         # 381 tests, about 30 seconds
cd web && npm test                       # 75 tests
cd web && npm run typecheck && npm run build
```

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| UI shows an error box or is empty | The API is not running, or is on another port. Check http://localhost:8000/api/health and `API_TARGET` |
| "Address already in use" | Something holds the port. Use a different one (section 7) or stop the other process |
| Status says "Incomplete data" on a fresh start | Expected: nothing has run yet. Press **Run now** |
| A domain tile says "Incomplete data" after you enabled it | Nothing has evaluated its rules yet: press **Run now** with tier **Close**. If the rules list is missing rules you expect, the API is running older code: restart it |
| The DCAA tile says "Not enabled" | The active config has no `domains:` list naming it. An existing database keeps the config it was created with, so add `- dcaa_cost_accounting` under `domains:` in **Rules & config** and save, or delete the database (section 6) to re-seed from the shipped file |
| Run fails with `missing_input` or `schema_mismatch` | `data/out/` is missing or partial. Run `python -m data.generate` |
| Replay says `Identical: false` and names a file | That data file, or the config, changed since the run. This is the intended guard; press *Run now* for a fresh run |
| `ModuleNotFoundError: api` (or `engine`) | Run the command from the `app/` folder, using `.venv/bin/python` |
| Numbers differ from this guide | Regenerate the data (`python -m data.generate`), delete the database (section 6) and restart the API |

## 11. What this is not

A demo over synthetic data. No real customer data, authentication, multi-tenancy, LLM calls or MCP server, and every
rule is marked unreviewed: no CPA has reviewed any citation or floor. See `app/README.md` for the full list of limits.
