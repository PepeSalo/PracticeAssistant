**You are a professional senior programmer that has a task to develop a standalone application or program strictly according to the plan.md and following instructions.md aways when reasonable.**

## Dev environment tips
- plan.md may be improved as needed but original content is to be followed strictly
- Create a virtual env. If env_xx folder is already existing in the active workspace - do not rename - install only dependencies in it: `python -m venv .venv && source .venv/bin/activate` (Windows: `.venv\Scripts\activate`).
- Install dependencies: `pip install -r requirements.txt`. For linting/formatting/tests, use
  `pip install -r requirements-dev.txt` instead.
- Set the relevant API key(s) before running: `export OPENAI_API_KEY="sk-..."`
  and/or `export ANTHROPIC_API_KEY="sk-ant-..."`. Never hard-code either,
  never commit `.env`.
- Create readme.md after all testing is complete even without asking


## Testing instructions
- Tests live in `tests/`; run with `pytest -q`.
- Never call a real LLM API or hit the network in tests — mock
  `requests.post` (see `tests/test_providers.py` for the pattern) and
  monkeypatch API-key env vars rather than relying on real credentials.
- Cover at minimum: schema validation (valid entry, empty
  `definitions`, missing/extra keys, wrong types), each provider
  (missing API key, successful call, non-200 HTTP response, malformed
  response shape), the provider factory (`get_provider`, including an
  unknown-name error), JSON extraction from the model's raw text
  (plain and ```-fenced), and the retry loop (succeeds first try,
  retries on invalid output, retries on malformed JSON, gives up after
  `MAX_ATTEMPTS`).
- Run `ruff check .` and `black --check .` before committing; fix
  anything flagged (`ruff check . --fix` and `black .` for the
  auto-fixable cases).
- The commit should pass `pytest`, `ruff`, and `black --check` before
  you merge.