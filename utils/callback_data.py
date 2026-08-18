"""Callback payloads, encoded as `action:value` (well under Telegram's 64-byte cap)."""


def make_callback(action: str, value: str = "") -> str:
    return f"{action}:{value}" if value else action


def parse_callback(data: str) -> tuple[str, str]:
    action, _, value = data.partition(":")
    return action, value
