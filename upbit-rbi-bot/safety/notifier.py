"""텔레그램 알림 (헌장 §9.5, §10.2). 미설정 시 콘솔 출력으로 폴백."""
from __future__ import annotations

from config.settings import settings

try:
    import requests
except ImportError:
    requests = None


class TelegramNotifier:
    def __init__(self):
        self.token = settings.telegram_token
        self.chat_id = settings.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id and requests)

    def send(self, message: str) -> None:
        if not self.enabled:
            print(f"[notify] {message}")
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": message},
                timeout=5,
            )
        except Exception as e:  # 알림 실패가 매매를 막지 않도록
            print(f"[notify-failed] {message} ({e})")
