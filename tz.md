# ТЗ: переписывание харнесса на LangGraph (v1 = PoC-скелет)

> Спецификация для **переписывания** текущего харнесса агентской разработки
> (`orchestrator/` + `.claude/agents/`) на новую архитектуру: детерминированный
> FSM-оркестратор на **LangGraph**, вызывающий недетерминированные роли через
> **подключаемые бэкенды** за единым интерфейсом, с единой observability на **Langfuse**.
> Это НЕ `plan.md` (тот — продуктовый артефакт системы). Этот файл написан вручную через интервью.

## Overview
- **Цель:** доказать на работающем скелете (PoC), что control-flow можно вынести в
  детерминированный LangGraph-граф, а роли запускать через сменные бэкенды за общим
  интерфейсом (`stub` и реальный `claude_sdk`), с единым трейсингом в Langfuse.
- **Для кого:** для нас — как фундамент будущего гибкого харнесса, независимого от
  `claude_agent_sdk` и от конкретной модели.
- **«Готово» для v1:** граф проходит **happy path** `DEV→REVIEW→TEST→(FINAL_REVIEW)→DONE`
  на двух бэкендах за одним интерфейсом; FSM-логика перенесена 1:1; прогон виден одним
  трейсом в self-hosted Langfuse; состояние графа лежит в Postgres-checkpointer, а
  человекочитаемая память между раундами — в `commit.md`.

## Архитектура (для ориентира ревьюеру)
- **Оркестратор** = `StateGraph` (LangGraph). Узлы — стадии, рёбра — детерминированные
  роутеры по вердиктам. Никакой LLM-маршрутизации (НЕ supervisor-паттерн).
- **Состояние** = гибрид: машинное состояние ветки (`BranchState`) персистится
  **Postgres-checkpointer**'ом LangGraph; `commit.md`/`metadata.yaml`/`main.md` —
  **рендеры** поверх (single-writer, оркестратор — единственный писатель).
- **Роль** запускается через `RoleBackend` (Protocol): `задача → RoleResult`. Граф не
  знает, какой бэкенд за интерфейсом.
  - `StubBackend` — возвращает fixture-вердикты (детерминированный прогон).
  - `ClaudeSdkBackend` — обёртка над `claude_agent_sdk.query()` + `output_format`.
- **VCS** абстрагирован за `VcsPort`; в PoC — `FakeVcs` (логирует вызовы).
- **Observability** — OTel-спаны в self-hosted Langfuse: один трейс на прогон ветки,
  вложенные спаны на узлы графа и на вызовы ролей (модель, стоимость, вердикт).

## Глобальные ограничения (инварианты)
- Python ≥ 3.12, строгая типизация (mypy-friendly), pydantic v2.
- LangGraph (граф + Postgres-checkpointer: `langgraph-checkpoint-postgres`).
- `claude-agent-sdk` (для `ClaudeSdkBackend`), PyYAML (рендер `metadata.yaml`).
- `langfuse` SDK + OpenTelemetry; Langfuse поднимается локально через `docker-compose`.
- Postgres — общий контейнер в `docker-compose` (отдельная БД под app-checkpointer).
- Тесты: pytest + pytest-asyncio.
- **Новый репозиторий с нуля**; `schemas`-контракты вердиктов и рендеры переносятся/пишутся заново.
- Детерминированный слой (граф/состояние/роутеры/курация) тестируется **без сети и без
  установленного `claude_agent_sdk`** (SDK импортируется лениво внутри бэкенда).

## Глобальные non-goals (v1)
- `OwnBackend` (собственный ReAct-цикл + свои тулзы + модель через polza.ai/OpenAI-агрегатор) — следующая веха.
- Реальный git (`GitVcs` через subprocess) — в PoC только `FakeVcs`.
- Демонстрация/тесты ветвей loopback, осцилляции, conflict, cap→ESCALATED — код их
  реализует (порт 1:1), но в критерии приёмки v1 входит только happy path.
- Резюмабельность (kill процесса → продолжение из checkpointer) — вне области PoC.
- Параллельный прогон нескольких веток, UI поверх Langfuse, прод-деплой.

## Branch scheme
- `main` — merge target; хранит код + `DEVLOG.md`.
- Порядок и зависимости:
  1. `feature_scaffold` → 2. `feature_state`, `feature_backend_iface`, `feature_vcs_port`
  (параллельно после scaffold) → 3. `feature_graph` → 4. `feature_roles` →
  5. `feature_claude_sdk_backend` → 6. `feature_observability` → 7. `feature_cli_e2e`.

## Features

### Scaffold   (branch: feature_scaffold, depends_on: —)
Spec:
- Раскладка пакета (напр. `harness/`), `pyproject.toml` со всеми зависимостями из
  «Глобальных ограничений», конфиг линтера/mypy, `README.md`.
- `docker-compose.yml`: сервис Langfuse (self-hosted) + Postgres (одна инстанция, две
  БД: `langfuse`, `app_checkpointer`). `.env.example` с переменными подключения.
- `config.py`: `CAP`, `MAX_TRANSITIONS`, `MAX_TURNS_PER_ROLE`, реестр ролей
  (`model`/`effort`/`allowed_tools`/`permission_mode`) — перенос смысла из текущего `config.py`.

Acceptance criteria (reviewer checks these):
- [ ] `pip install -e .` ставит проект без ошибок; `mypy` проходит на пустом скелете.
- [ ] `docker-compose up` поднимает Langfuse (UI доступен локально) и Postgres с двумя БД.
- [ ] `config.py` содержит 5 ролей с моделью/effort/tools; значения `CAP/MAX_TRANSITIONS/MAX_TURNS_PER_ROLE` заданы.
- [ ] Импорт пакета не тянет `claude_agent_sdk` (проверяется тестом при отсутствии SDK).

Non-goals:
- Любая бизнес-логика графа/ролей.

Constraints:
- Все версии зависимостей зафиксированы в `pyproject.toml`.

### State & store   (branch: feature_state, depends_on: feature_scaffold)
Spec:
- `BranchState` — типизированная схема состояния ветки (стадия, `loop_a/b_rounds`,
  `open_issues`, `rejected`, `resolved_sigs`, `conflicts`, `r/t_counter`, последние
  вердикты, `cost_usd`, `history`). STAGES включает `FINAL_REVIEW` (порт 1:1).
- Подключение **Postgres-checkpointer** LangGraph как хранилища `BranchState`.
- Рендеры (single-writer): `render_commit`→`commit.md`, `render_metadata`→`metadata.yaml`
  (через `yaml.safe_dump`), `render_main`→`main.md` (дашборд по веткам). Рендер
  детерминирован из состояния (никогда не дописывается, не устаревает).

Acceptance criteria (reviewer checks these):
- [ ] `BranchState` сериализуется/десериализуется через Postgres-checkpointer (round-trip тест).
- [ ] `render_commit/metadata/main` дают идентичный вывод при одинаковом `BranchState` (детерминизм).
- [ ] `metadata.yaml` валиден как YAML и содержит стадию, счётчики раундов, последние вердикты, причину эскалации.
- [ ] Запись `commit.md` всегда полная (тест: два разных состояния → два полных, не накопительных файла).

Non-goals:
- Чтение/запись из ролей (роли пишут только код через свои тулзы; здесь — слой оркестратора).

Constraints:
- Оркестратор — единственный писатель под каталогом состояния ветки.

### Backend interface + stub   (branch: feature_backend_iface, depends_on: feature_scaffold)
Spec:
- `schemas.py`: pydantic-контракты вердиктов (`ReviewerVerdict`/`ReviewIssue`,
  `TesterVerdict`/`TesterFailure`, `DevStatus` с `dev_status: Literal["green","blocked"]`),
  `ROLE_SCHEMA: dict[role → schema | None]`. Перенос смысла из текущего `schemas.py`.
- `RoleResult` (dataclass): `structured: dict | None`, `text: str`, `subtype: str | None`, `cost_usd: float`.
- `RoleBackend` (Protocol): `async run(role: str, task: str, *, context) -> RoleResult`.
  `context` даёт бэкенду root/branch/пути только для чтения.
- `StubBackend`: возвращает вердикты из переданного fixture-набора (по роли и номеру
  вызова), `cost_usd=0`. Структурный вывод валидируется против `ROLE_SCHEMA`.

Acceptance criteria (reviewer checks these):
- [ ] `RoleBackend` — формальный `typing.Protocol`; `StubBackend` ему удовлетворяет (mypy).
- [ ] `StubBackend.run("reviewer", ...)` возвращает `RoleResult` с `structured`, валидным по `ReviewerVerdict`.
- [ ] Невалидный fixture-вердикт отклоняется валидацией (тест на ошибку схемы).
- [ ] Контракт `RoleResult` стабилен и не зависит от типа бэкенда (один тест гоняет stub и mock-claude через общий код).

Non-goals:
- `ClaudeSdkBackend`, `OwnBackend`.

Constraints:
- Никаких сетевых вызовов; чистый детерминированный модуль.

### VCS port   (branch: feature_vcs_port, depends_on: feature_scaffold)
Spec:
- `VcsPort` (Protocol): `checkout_branch`, `head`, `commit(paths, message)`, `diff()`.
- `FakeVcs`: реализация, которая логирует вызовы в память (для проверок) и пишет
  отрендеренные файлы на диск, но не выполняет реальный git.

Acceptance criteria (reviewer checks these):
- [ ] `FakeVcs` удовлетворяет `VcsPort` (mypy).
- [ ] После «коммита» `FakeVcs` фиксирует переданные пути и сообщение (проверяемо в тесте).
- [ ] Граф взаимодействует с git только через `VcsPort` (нет прямых вызовов subprocess вне `GitVcs`).

Non-goals:
- `GitVcs` (реальный subprocess) — следующая веха.

Constraints:
- Интерфейс должен покрывать всё, что нужно текущему `_git/_commit/_head/diff`.

### Graph (FSM)   (branch: feature_graph, depends_on: [feature_state, feature_backend_iface, feature_vcs_port])
Spec:
- `StateGraph` поверх `BranchState`: узлы стадий `DEV/REVIEW/TEST/FINAL_REVIEW/DONE/ESCALATED`
  + узел-предшаг `PLAN` (planner, вне петель).
- Детерминированные роутеры (обычные функции, без LLM): `green→REVIEW`, `blocked→ESCALATED`,
  `CHANGES_REQUESTED→DEV`, `PASS(review)→TEST`, `FAILED→DEV`, `PASS(test)→FINAL_REVIEW|DONE`
  (условие `_can_skip_final`).
- Перенос курации 1:1: `_sig`/`resolved_sigs` (осцилляция), `_drop_source`/`_add_issue`
  (R-/T- id, замена issue по источнику за раунд), `apply_reviewer`/`apply_tester`,
  cap по `CAP`, hard-stop по `MAX_TRANSITIONS`, причины эскалации
  (`oscillation`/`cap_exceeded`/`developer_blocked`/`no_verdict`/`max_transitions`/`conflict`).
- Узлы зовут роли через `RoleBackend`, git — через `VcsPort`, пишут состояние через store.

Acceptance criteria (reviewer checks these):
- [ ] На `StubBackend` с PASS-вердиктами граф проходит `DEV→REVIEW→TEST→(FINAL_REVIEW)→DONE` (happy path E2E тест).
- [ ] Все причины эскалации и роутеры реализованы в коде (покрыты unit-тестами роутер-функций, без полного прогона).
- [ ] Граф не делает ни одного LLM-вызова для маршрутизации (проверяется: роутеры — чистые функции от `BranchState`).
- [ ] `MAX_TRANSITIONS` гарантированно останавливает прогон (unit-тест на счётчик переходов).

Non-goals:
- Полные E2E-прогоны loopback/осцилляции/conflict/cap (только unit на роутерах в v1).

Constraints:
- Ноль модельных токенов в управляющем слое.

### Roles (prompts + stubs + config)   (branch: feature_roles, depends_on: feature_backend_iface)
Spec:
- 5 ролей: `developer`, `reviewer`, `tester`, `summarizer`, `planner`. Для каждой —
  prompt-файл `.claude/agents/<role>.md` (перенос/адаптация текущих) и per-role tuning в `config.py`.
- Fixture-наборы вердиктов для `StubBackend` под happy-path сценарий каждой роли.
- `planner` — предшаг: на вход спека, на выход `plan.md` (в PoC — stub-вердикт/фиктивный план).

Acceptance criteria (reviewer checks these):
- [ ] Для каждой из 5 ролей есть prompt-файл и запись в реестре ролей (`config.py`).
- [ ] Happy-path fixture-набор покрывает полный цикл (developer→reviewer→tester→summarizer) + planner.
- [ ] `planner` в составе графа отрабатывает ДО стадии `DEV` и порождает `plan.md` (проверяемо на stub).

Non-goals:
- Реальные тулзы ролей (это бэкенд-специфично; `claude_sdk` — отдельная веха).

Constraints:
- Prompt-файлы — продуктовые артефакты; их формат совместим с `claude_agent_sdk` (frontmatter).

### Claude SDK backend   (branch: feature_claude_sdk_backend, depends_on: [feature_backend_iface, feature_roles])
Spec:
- `ClaudeSdkBackend(RoleBackend)`: ленивый импорт `from claude_agent_sdk import
  ClaudeAgentOptions, ResultMessage, query`; собирает opts (system_prompt из
  `.claude/agents/<role>.md`, model/effort/allowed_tools/permission_mode,
  `setting_sources=["project"]`, cwd, `max_turns`); при наличии схемы — `output_format`
  (json_schema). Читает только `ResultMessage` → `RoleResult`.

Acceptance criteria (reviewer checks these):
- [ ] `ClaudeSdkBackend` удовлетворяет `RoleBackend` (mypy); код детерминированного ядра импортируется без установленного SDK.
- [ ] При наличии схемы роли в opts передаётся `output_format` с `model_json_schema()`.
- [ ] Smoke-прогон: граф с `ClaudeSdkBackend` на маленькой реальной задаче достигает `DONE` (happy path, помечен как сетевой/опциональный тест).
- [ ] Тот же граф без изменений работает и со `StubBackend`, и с `ClaudeSdkBackend` (доказательство подключаемости).

Non-goals:
- Тонкая настройка промптов/тулзов под качество; biling-режимы.

Constraints:
- По умолчанию — `ANTHROPIC_API_KEY` (рекомендованный путь для программного запуска);
  подписочный режим — опция (учитывая грядущее разделение биллинга Agent SDK).

### Observability (Langfuse)   (branch: feature_observability, depends_on: feature_graph)
Spec:
- OTel-инструментирование: один трейс на прогон ветки; вложенные спаны на узлы графа
  (стадия, переход) и на вызовы ролей (`role`, `model`, `cost_usd`, `subtype`, вердикт).
- Экспорт в self-hosted Langfuse через SDK/OTLP (адрес из `.env`).

Acceptance criteria (reviewer checks these):
- [ ] После happy-path прогона в Langfuse виден один трейс со вложенными спанами всех стадий.
- [ ] Спан вызова роли содержит `role`, использованный бэкенд, `cost_usd` и итоговый вердикт.
- [ ] `claude_sdk`-роль даёт хотя бы один (грубый) спан со стоимостью из `ResultMessage`; `stub` — спан с `cost_usd=0`.
- [ ] Суммарная `cost_usd` прогона в трейсе совпадает с `BranchState.cost_usd`.

Non-goals:
- Дашборды/алерты/evals в Langfuse; трассировка внутренних tool-call'ов `claude_sdk` (чёрный ящик).

Constraints:
- Инструментирование не должно ломать прогон при недоступном Langfuse (graceful degrade).

### CLI + end-to-end   (branch: feature_cli_e2e, depends_on: [feature_graph, feature_roles, feature_claude_sdk_backend, feature_observability])
Spec:
- CLI-вход: `python -m harness <branch> [--base main] [--root .] [--backend stub|claude_sdk]`.
- Полный happy-path прогон: `PLAN → DEV → REVIEW → TEST → (FINAL_REVIEW) → DONE`,
  состояние в Postgres-checkpointer, рендеры на диск через `FakeVcs`, трейс в Langfuse.

Acceptance criteria (reviewer checks these):
- [ ] `python -m harness demo --backend stub` детерминированно доходит до `DONE` и пишет `commit.md`/`metadata.yaml`/`main.md`.
- [ ] Тот же запуск с `--backend claude_sdk` доходит до `DONE` на маленькой реальной задаче (опциональный сетевой тест).
- [ ] По завершении `metadata.yaml` показывает `stage: DONE` и непустую историю переходов.
- [ ] Прогон виден одним трейсом в Langfuse (ссылка/скрин в `DEVLOG.md`).

Non-goals:
- Прод-упаковка, аутентификация, многократные/параллельные ветки.

Constraints:
- CLI — тонкая обёртка над графом (argparse + asyncio.run), без бизнес-логики.
