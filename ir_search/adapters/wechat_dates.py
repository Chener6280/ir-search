from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


WECHAT_TZ = timezone(timedelta(hours=8))


def parse_wechat_published_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    now = datetime.now(WECHAT_TZ)
    if text == "昨天":
        return now - timedelta(days=1)
    if text == "前天":
        return now - timedelta(days=2)
    if text.endswith("小时前"):
        try:
            return now - timedelta(hours=int(text[:-3]))
        except ValueError:
            return None

    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=WECHAT_TZ)
        except ValueError:
            continue
    return None
