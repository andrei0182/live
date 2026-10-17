from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def send(text: str, token: str | None = None, chat_id: str | None = None, timeout: float = 15.0) -> bool:
    """Trimite un mesaj prin botul Telegram. Citește TELEGRAM_BOT_TOKEN /
    TELEGRAM_CHAT_ID din mediu dacă nu sunt date explicit."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID lipsesc, mesajul nu a fost trimis:\n%s", text)
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        # nu logăm excepția întreagă: conține URL-ul, deci tokenul botului
        logger.error("Telegram a eșuat: %s", type(exc).__name__)
        return False
    if not resp.ok:
        logger.error("Telegram a răspuns %s: %s", resp.status_code, resp.text[:200])
        return False
    return True
