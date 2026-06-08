# change_88 — pytest и README под собес

## Что сделано

- `tests/` — 22 pytest-теста: agent router, HR parse, health probes (без Mistral/Telegram).
- `pyproject.toml` — dev-группа pytest, `pythonpath`, asyncio mode.
- `.github/workflows/ci.yml` — шаг `pytest -v`.
- `README.md` — таблица навыков, архитектура, команда тестов, live demo.
- `screenshots/` — каталог под PNG для GitHub.

## Проверка

```bash
uv sync && uv run pytest -v
```
