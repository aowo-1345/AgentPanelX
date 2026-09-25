"""Minimal outbound Feishu webhook client."""

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class FeishuWebhookError(RuntimeError):
    """A webhook could not accept a notification."""


@dataclass(frozen=True, slots=True)
class FeishuWebhookClient:
    """Send text messages to one HTTPS Feishu bot webhook."""

    webhook_url: str
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.webhook_url.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Feishu webhook URL must be an HTTPS URL")
        if self.timeout_seconds <= 0:
            raise ValueError("Feishu webhook timeout must be positive")

    def send(self, text: str) -> None:
        payload = json.dumps(
            {"msg_type": "text", "content": {"text": text}},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if not isinstance(status, int) or not 200 <= status < 300:
                    raise FeishuWebhookError(f"Feishu webhook returned HTTP {status}")
        except HTTPError as error:
            raise FeishuWebhookError(f"Feishu webhook returned HTTP {error.code}") from error
        except (OSError, URLError, TimeoutError) as error:
            raise FeishuWebhookError("Feishu webhook request failed") from error
