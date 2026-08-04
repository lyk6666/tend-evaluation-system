# TEND Evaluation System

TEND Evaluation System is a local-first research dashboard for running the official
[TEND](https://github.com/Jinwei-Lu/Text-to-NoSQL) baselines and full SAG v3 solver,
monitoring long benchmark runs, and inspecting EXC, EXF1, claim-axis, and outcome results.

The top-bar **New Methods** workspace implements the complete retrieval-to-query workflow
in four tabs:

1. **Bundle extraction** persists six inspectable artifacts: normalization,
   retrieval-target extraction, supporting-field inference, restriction binding, the
   retrieval graph, and the final `RetrievalBundle`. The minimal bundle contains only the
   original `question`, retrieval `targets`, structural `relations`, and directly useful
   `value_constraints`; it has no duplicated `mention` field.
2. **Index building** traverses the migrated full schema tree for any of the 11 databases,
   scans exact MongoDB values, profiles dynamic keys and arrays, infers declared or strict
   high-confidence ID references, generates search/context embeddings with retryable
   checkpoints, and publishes an index only after completeness validation. Builds can be
   paused, resumed, or cancelled.
3. **Schema pruning** compiles one minimal search request per target, exposes every local
   scoring signal, evaluates structural and reference relationships, performs joint beam
   search, and keeps the best plus near-best schema alternatives. Every intermediate stage
   survives refresh. Final connected schemas are available as zoomable, selectable trees.
4. **MQL generation** sends the question and each selected compact pruned-schema alternative
   directly to the configured model. It persists independent candidate pipelines, enforces
   read-only and pruned-path constraints, validates `$lookup` stages against supplied
   reference edges, probes MongoDB, performs a bounded error-grounded repair, executes every
   valid candidate, and selects the highest-scoring successful schema alternative.

Only entities, fields, and requested derived concepts become retrieval targets. Temporal
values and constants constrain path scoring. Aggregation, grouping, sorting, tie handling,
and derived-query construction are handled by MQL generation rather than schema retrieval;
units, duplicate representations, and EAV layouts remain outside the current retrieval
scope.

The MQL workspace shows six inspectable stages: input preparation, candidate generation,
deterministic validation, bounded repair, execution and selection, and TEND evaluation.
Its final view presents four TEND result dimensions: EXC, EXF1, the mutually exclusive
outcome bucket, and claim-axis slices. These are computed only when the selected database
and question exactly match an official canonical or colloquial TEND record. A custom
question has no gold query, so the UI reports all four dimensions as unavailable instead
of fabricating correctness scores.

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
the adjacent `TEND_QueryCraft` directory. Use a real CPython installation, not the Windows
Store `python.exe` app-execution alias.

```powershell
Copy-Item .env.example .env
$pythonExecutable = "C:\Path\To\CPython\python.exe"
& $pythonExecutable --version
& $pythonExecutable -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip install -e "..\TEND_QueryCraft"
pnpm.cmd --dir frontend install
```

Start the API and UI in separate terminals:

Terminal 1:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
```

Terminal 2:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-frontend.ps1
```

The backend script invokes `.venv\Scripts\python.exe` directly and verifies its compiled
dependencies before starting, so it cannot silently fall back to a global Python
interpreter. The frontend is independent of Python and checks only pnpm and its installed
Node dependencies.

The API is served at `http://127.0.0.1:8000`; the dashboard is served at
`http://127.0.0.1:5173`.

### Repair a broken virtual environment

`ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'` almost always means
the virtual environment was created by a different or unavailable Python interpreter. Stop
the backend first, then recreate the environment with a working CPython 3.11+ executable:

```powershell
Remove-Item .venv -Recurse -Force
& "C:\Path\To\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip install -e "..\TEND_QueryCraft"
pnpm.cmd --dir frontend install
```

The commands above do not require virtual-environment activation. The `-ExecutionPolicy
Bypass` flag applies only to the child PowerShell process used to launch each checked-in
start script; it does not change the user's persistent execution policy.

Do not recreate `.venv` with the Windows Store Python app-execution alias. In particular,
mixing a Python 3.13 environment with a `pydantic_core` wheel built for Python 3.12 causes
this exact module-not-found error.

When a benchmark run finishes (including an intentionally cancelled or partially failed
run), the service automatically invokes the official TEND evaluator once per selected
track. The Results workspace displays per-method EXC and EXF1, claim-axis slices, the
mutually exclusive outcome decomposition, and record-level diagnostics. Official report
JSON/Markdown and per-record CSV/JSONL are downloadable from the same workspace.

To verify the evaluator against the existing MongoDB databases without making a provider
request, run the one-record gold-query smoke test. It defaults to official record 362579,
the slow gold pipeline that catches accidental reuse of the solver's 30-second probe cap:

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
TEND_EVAL_MAX_GENERATION_ATTEMPTS=2
TEND_EVAL_GENERATION_MONGO_MAX_TIME_MS=30000
TEND_EVAL_EMBEDDING_API_KEY=
TEND_EVAL_EMBEDDING_MODEL=text-embedding-3-small
TEND_EVAL_EMBEDDING_BASE_URL=https://api.openai.com/v1
TEND_EVAL_MONGO_MAX_TIME_MS=120000
```

`TEND_EVAL_EMBEDDING_API_KEY` is separate so indexing can use a distinct provider key; if
left empty it falls back to `OPENAI_API_KEY`. An empty, malformed, or dimensionally
inconsistent embedding response never completes a build. The request is retried according
to `TEND_EVAL_PROVIDER_MAX_RETRIES`, and completed batches remain resumable checkpoints.

MQL provider calls use the same retry policy. `TEND_EVAL_MAX_GENERATION_ATTEMPTS` bounds
non-empty structured-output attempts for each candidate or repair, while
`TEND_EVAL_GENERATION_MONGO_MAX_TIME_MS` bounds validation probes and result previews. The
repair-attempt count and preview-row limit remain configurable per run in the UI.

Validated indexes are stored under
`data/runtime/schema_indexes/<database-id>/<index-id>/`. Exact value membership remains in
SQLite beside min/max profiles, so (for example) a value of `25` is reported as unobserved
when the data contains only `0, 10, 20, 30, 50`, even though `25` lies inside the range.

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
the denominator to the selected databases. It follows the upstream evaluator layout and
links the official witness and schema files into the staging release; on the normal
same-volume setup, hardlinks avoid duplicating the multi-gigabyte export. The evaluator
reuses the already loaded exact-name MongoDB databases. `TEND_EVAL_MONGO_MAX_TIME_MS`
defaults to 120 seconds because some official gold pipelines exceed the upstream solver's
30-second probe budget on commodity machines.

The public upstream checkout currently omits its referenced
`proposals/schemas/solver_allow_list.json`. The compatibility boundary allows the public
methods to execute, but exported disclosures explicitly mark model-disjointness as
unverifiable. This system never silently upgrades that condition to a passing claim.

The paper's reported table used DeepSeek-V4-Flash with maximum reasoning effort. Runs with
GPT-5.6 Luna are new controlled evaluations using the same model across selected methods;
they should not be presented as exact reproductions of the paper's headline numbers.
