---
name: product-fullstack-engineering
description: Full-stack and data engineering for Python/FastAPI backends, aiogram Telegram bots, PostgreSQL, AI workflows (prompts externalized), resume/repo parsing, trust-based communities, and pragmatic MVP deployment. Use for architecture, implementation, refactors, or product tradeoffs in this stack.
---

# Product full-stack engineering

## When to use

- Building or changing backend APIs, bots, data pipelines, or AI-assisted features in this ecosystem.
- Designing schemas, community mechanics, or deployment for an MVP.
- The user asks for conventions, tradeoffs, or “how we do things here.”

## General development

- Write simple, readable, maintainable code; prefer practical solutions over complex abstractions.
- Build MVP first; improve after validation.
- Analyze problems before implementing; remove dead code; keep structure minimal and clear.
- Refactor when support cost rises, not preemptively.

## Python backend

- Python 3.11+; FastAPI for HTTP APIs.
- Use `async` / `await` only where it pays off (I/O-bound paths, framework patterns).
- Prefer plain functions for business logic; use OOP only when it simplifies maintenance.
- Configuration via environment variables and explicit config loading.

## Telegram bots (aiogram)

- Clear user flows and command structure.
- FSM only when conversation state is truly required.
- Handlers stay thin: parse/update UI → call services → send response. No business logic in handlers.
- Include admin flows for moderation and internal ops when relevant.

## Database (PostgreSQL)

- Schemas and relations that stay understandable at scale.
- SQLAlchemy or SQLModel without unnecessary indirection; prefer readable queries over clever ORM.
- Avoid duplicating invariants in multiple layers; keep data safety in mind.

## AI integration

- Workflows for resume analysis, recommendations, and similar tasks.
- Prompts live outside core business logic; call AI through dedicated functions/modules.
- Iterate prompts with tests; parse structured outputs; validate before persisting or acting.
- Optimize tokens and API cost.

## Data engineering

- Parse PDF, DOCX, and plain text.
- Integrate GitHub and other HTTP APIs.
- Extract and normalize metadata from resumes and repositories.
- Use simple async background work for heavy jobs.

## Product thinking

- Solve real pain, not feature count.
- Manually validate before automating.
- Prioritize by business value; cut low-value scope early.
- Consider retention, trust, and long-term value.

## Community system design

- Trust-first closed communities.
- Contribution models that resist abuse and farming.
- Ranking from real value, not vanity metrics.
- Referral and reputation: design carefully to avoid gaming.

## Deployment

- Ship MVP quickly with Railway, Render, or a VPS when that is enough.
- Avoid premature infra complexity; favor easy debug and restart.

## Documentation

- Short technical notes after meaningful updates.
- After important changes, add `change_N.md` (what, why, chosen approach, future improvements).
- Document decisions and non-obvious behavior, not every line of code.
- Keep docs brief and current.
