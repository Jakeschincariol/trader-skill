# NITUB — paper trading dashboard

Локальный веб-интерфейс с **вкладками** для наблюдения за paper-сессией NITUB.  
Без длинной простыни: каждая вкладка — один тип данных.

## Как открыть

```bash
cd dashboard
npm install
npm run dev
```

Откройте: **http://127.0.0.1:5173/**

Vite слушает `127.0.0.1:5173` (`strictPort`). Polling ~4 с через `/api/session/bundle`.

## Вкладки

| Вкладка | Что показывает |
| --- | --- |
| **Обзор** | P&L, equity, cash, число позиций и сделок |
| **Сделки** | таблица paper fills |
| **Портфель** | позиции + unrealized/realized |
| **События** | короткая лента из `events.jsonl` |
| **Анализ** | тезис, риски, краткие skill-строки |
| **Бэктесты** | `backtest.json` и/или `nitub/state/backtests/*.json` |

Активная вкладка запоминается в `localStorage`.

## Откуда данные

Только чтение из `../nitub/state/` (fallback: `../paper-trading/session-1/`).

| URL | Что отдаёт |
| --- | --- |
| `/api/session/` | список файлов |
| `/api/session/bundle` | JSON: portfolio, ledger, report, events, analysis, skills, backtest(s) |
| `/api/session/<файл>` | сырой файл |

Дашборд **не пишет** в state и не меняет логику `nitub/`.

Ожидаемые файлы:

- `portfolio.json` — позиции, fills, P&L
- `events.jsonl` — лента
- `analysis.json` / `report.md` — тезис и риски
- `skills.json` — краткие результаты skills
- `backtest.json` или каталог `backtests/` — результаты бэктестов

## Стек

Vite + React + TypeScript. Шрифты Syne + IBM Plex Sans. Стили — CSS-переменные, вкладки, лёгкие motion (появление панели, пульс P&L, live-dot).
