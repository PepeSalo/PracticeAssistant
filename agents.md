**You are a professional senior programmer that has a task to develop a standalone application or program strictly according to the plan.md and following instructions.md aways when reasonable.**

## Dev environment tips
- Version numbering starts at 1.0. Increase by exactly 0.1 for each subsequent completed change set (including code, configuration or documentation changes), not for each intermediate edit. Update the README's displayed version and version-history entry, and synchronize `pyproject.toml`'s project version. Use decimal arithmetic: after 1.9 comes 2.0, not 1.10. Record the date and a concise description; do not invent past releases.
- plan.md may be improved as needed but original content is to be followed strictly
- Record agent LLM model attribution for generated code and documents in the README; do not claim an unknown model version.
- Reuse the existing Python environment. An `env_xx`/`venv_xx` directory (the suffix may vary, e.g. `venv_10`) is an environment to preserve, not a reason to create another one. If the active workspace itself is named `env_xx` or `venv_xx`, treat that directory as both the project workspace and the environment root. Do not create a nested `.venv`, `venv`, or other environment directory inside it.
- Put installed packages in `<environment>\Lib\site-packages\`; install packages into the environment using `<environment>\Scripts\python.exe -m pip ...` or its activation command, not into `Scripts` itself.
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