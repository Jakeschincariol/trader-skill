# NITUB — бумажный дашборд

Локальный веб-интерфейс с вкладками для наблюдения за **бумажной** (симуляционной) сессией NITUB.  
Живые ордера и брокер не используются.

## Как открыть

```bash
cd dashboard
npm install
npm run dev
```

Откройте: **http://127.0.0.1:5173/**

Vite слушает `127.0.0.1:5173` (`strictPort`). Опрос сессии ~4 с через `/api/session/bundle` и статуса воркера через `/api/control/status`.

## Вкладки

| Вкладка | Что показывает |
| --- | --- |
| **Обзор** | кнопки **Старт** / **Стоп**, прибыль/убыток, капитал, наличные, позиции и сделки |
| **Сделки** | таблица бумажных исполнений |
| **Портфель** | позиции + нереализованный / реализованный результат |
| **События** | короткая лента из `events.jsonl` |
| **Анализ** | тезис, риски, краткие строки навыков |
| **Бэктесты** | `backtest.json` и/или `nitub/state/backtests/*.json` |

Активная вкладка запоминается в `localStorage`.

## Старт / Стоп

В шапке **Обзора** всегда видны кнопки:

| Кнопка | Действие |
| --- | --- |
| **Старт** | запускает бумажный воркер (`nitub.runner`): при пустом портфеле — один цикл `run paper --use-research-strategy`, затем периодическая переоценка (Yahoo) и лёгкий ребаланс к research-sleeve. Цель — *пытаться* вырастить ~$100 в симуляции (без обещания прибыли). |
| **Стоп** | ставит stop-флаг и завершает процесс по `pid`. |

Статус пишется в `nitub/state/runner.json` (`running`, `started_at`, `pid`, `goal`, `last_action`, `message` по-русски). Повторный Старт при уже работающем воркере идемпотентен.

| URL | Метод | Что делает |
| --- | --- | --- |
| `/api/control/start` | POST | старт воркера |
| `/api/control/stop` | POST | остановка |
| `/api/control/status` | GET | текущий `runner.json` |

CLI вручную (то же самое):

```bash
cd /Users/bugatti/Desktop/Projects/NITUB
export PYTHONPATH="$PWD" NITUB_MODE=paper
nitub/.venv/bin/python -m nitub.runner --interval 45 --budget 100
```

## Откуда данные

Чтение из `../nitub/state/` (fallback: `../paper-trading/session-1/`). Управление воркером пишет только `runner.json`, `runner.stop`, обновления `portfolio.json` / `events.jsonl` / `ledger.md` / `meta.json` через paper-воркер.

| URL | Что отдаёт |
| --- | --- |
| `/api/session/` | список файлов |
| `/api/session/bundle` | JSON: portfolio, ledger, report, events, analysis, skills, backtest(s) |
| `/api/session/<файл>` | сырой файл |

Ожидаемые файлы:

- `portfolio.json` — позиции, сделки, P&L
- `events.jsonl` — лента
- `analysis.json` / `report.md` — тезис и риски
- `skills.json` — краткие результаты навыков
- `backtest.json` или каталог `backtests/` — результаты бэктестов
- `runner.json` — статус бумажного воркера

## Стек

Vite + React + TypeScript. Шрифты Syne + IBM Plex Sans. Стили — CSS-переменные, вкладки, лёгкие motion (появление панели, пульс P&L, индикатор статуса).
