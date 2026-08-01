# TEND Evaluation System

TEND Evaluation System is a local-first research dashboard for running the official
[TEND](https://github.com/Jinwei-Lu/Text-to-NoSQL) baselines and full SAG v3 solver,
monitoring long benchmark runs, and inspecting EXC, EXF1, claim-axis, and outcome results.

The system keeps the official TEND implementation as an external checkout. It does not
vendor or republish upstream source code. The adapter records the upstream commit for every
run so results remain reproducible.

## Target workload

- Canonical track: 110 tasks per database across 11 databases (1,210 tasks per method).
- Robustness track: the same records using `NLQ_colloquial` (another 1,210 per method).
- Methods: all nine registered TEND baselines, compatible self-consistency variants, and
  full SAG v3. SAG ablations are intentionally excluded.

## Local development

Requirements: Python 3.11+, Node.js 18+, pnpm, MongoDB, and the official TEND checkout in
the adjacent `TEND_QueryCraft` directory.

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip install -e "..\TEND_QueryCraft"
pnpm --dir frontend install
```

Start the API and UI in separate terminals:

```powershell
.\scripts\start-backend.ps1
.\scripts\start-frontend.ps1
```

The API is served at `http://127.0.0.1:8000`; the dashboard is served at
`http://127.0.0.1:5173`.

When a benchmark run finishes (including an intentionally cancelled or partially failed
run), the service automatically invokes the official TEND evaluator once per selected
track. The Results workspace displays per-method EXC and EXF1, claim-axis slices, the
mutually exclusive outcome decomposition, and record-level diagnostics. Official report
JSON/Markdown and per-record CSV/JSONL are downloadable from the same workspace.

To verify the evaluator against the existing MongoDB databases without making a provider
request, run the one-record gold-query smoke test:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_evaluation.py
```

Never commit `.env`, API keys, MongoDB data, generated predictions, or run artifacts.

## Provider configuration

The default run-level provider configuration is OpenAI-compatible Chat Completions with
`gpt-5.6-luna` and `medium` reasoning. Add your key only to `.env`:

```dotenv
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
TEND_EVAL_MODEL=gpt-5.6-luna
TEND_EVAL_REASONING_EFFORT=medium
```

For a no-cost plumbing check, set `TEND_EVAL_LLM_STUB=1`. Stub output verifies method,
MongoDB, persistence, control, and UI paths, but it is not a benchmark result.

GPT-5.6 Luna supports Chat Completions, structured output, streaming, and reasoning efforts
from `none` through `max`. See the [official model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

## Reproducibility and artifacts

Every run writes a secret-free manifest and append-only prediction JSONL under
`data/runtime/runs/<run-id>/`. The official TEND transcript tree is kept separately under
`data/runtime/official-tend-runs/<run-id>/`.

Evaluation artifacts are stored by track under
`data/runtime/runs/<run-id>/evaluation/<track>/report/`. A run-scoped release subset fixes
the denominator to the selected databases. Because the evaluator is configured to reuse
the existing MongoDB databases, its staging release uses empty witness mappings and does
not parse the 5.3 GB raw export a second time.

The public upstream checkout currently omits its referenced
`proposals/schemas/solver_allow_list.json`. The compatibility boundary allows the public
methods to execute, but exported disclosures explicitly mark model-disjointness as
unverifiable. This system never silently upgrades that condition to a passing claim.

The paper's reported table used DeepSeek-V4-Flash with maximum reasoning effort. Runs with
GPT-5.6 Luna are new controlled evaluations using the same model across selected methods;
they should not be presented as exact reproductions of the paper's headline numbers.
