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

Never commit `.env`, API keys, MongoDB data, generated predictions, or run artifacts.
