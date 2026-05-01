# change_13

## Что сделано

- Файл **`config/hr_sheet_context.json`**: список типичных работодателей для подсказки модели + **`venues_not_companies`** (в т.ч. Future Today) — явный запрет подставлять их в поле `company`.
- **`services/hr_context_config.py`** — читает JSON и подставляет блоки в промпт.
- **`prompts/hr_extract.txt`** и **`services/llm.extract_hr`** — правила: `company` только работодатель; площадка знакомства только в `summary_ru`; при неизвестном работодателе `company: null`.
- Тексты: **`/start`**, справка в **`messages._help_reply`**, ответ после приёма контакта в **`hr_service`**.

## Зачем

- Лист Google Sheets создаётся по `company`; путаница «Future Today = компания» давала неверные вкладки. Теперь и промпт, и UX разводят работодателя и место знакомства.

## Позже

- Расширять `employers_hint` под ваш пул компаний; при необходимости вынести `venues_not_companies` в отдельный короткий список без общих слов.
