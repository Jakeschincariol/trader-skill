"""Gated live stub — never places real orders. Requires explicit flags + broker keys (absent)."""

from __future__ import annotations

import os

from .paths import MODE_ENV


REFUSAL = """\
LIVE MODE ОТКЛОНЁН.

NITUB по умолчанию работает только в paper (`NITUB_MODE=paper`).
Живые ордера намеренно не реализованы и не будут отправлены.

Чтобы даже запросить live-заглушку, нужно одновременно:
  1) флаг CLI: --i-really-want-live
  2) NITUB_MODE=live
  3) брокер-ключи (ALPACA_API_KEY / ALPACA_SECRET_KEY) — сейчас их нет и ставить не нужно

Без ключей и без явного флага live всегда отказывается.
Никаких реальных ордеров. Используйте: nitub run paper
"""


def refuse_live(*, explicit_flag: bool = False) -> str:
    mode = (os.environ.get(MODE_ENV) or "paper").lower()
    has_keys = bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"))

    reasons = []
    if mode != "live":
        reasons.append(f"{MODE_ENV}={mode!r} (нужно live)")
    if not explicit_flag:
        reasons.append("нет флага --i-really-want-live")
    if not has_keys:
        reasons.append("нет брокер-ключей (и это правильно для NITUB)")

    # Always refuse — even if somehow all gates pass, live is a stub.
    body = REFUSAL
    if reasons:
        body += "\nТекущие причины отказа:\n- " + "\n- ".join(reasons) + "\n"
    body += "\nLive execution stub: NOT IMPLEMENTED. Paper only.\n"
    return body
