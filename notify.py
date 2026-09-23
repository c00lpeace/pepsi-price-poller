#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
특가 알림 발송기 (선택 기능)
- poll.py가 만든 alert.json을 읽어 알림이 있으면 ntfy.sh로 푸시
- 환경변수 NTFY_TOPIC 이 비어 있으면 조용히 종료 (알림 미사용)
- 실행: NTFY_TOPIC=내토픽 python3 notify.py
"""
import json
import os
import urllib.request

ALERT_FILE = "alert.json"

TYPE_LABEL = {
    "new_low": "최저가 경신",
    "sudden_drop": "급락 감지",
    "out_of_stock": "품절",
    "back_in_stock": "재입고",
}


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    try:
        with open(ALERT_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("alert.json 없음, 알림 건너뜀")
        return 0
    alerts = data.get("alerts", [])
    if not alerts:
        print("알림 없음")
        return 0
    if not topic:
        print(f"NTFY_TOPIC 미설정, 알림 {len(alerts)}건 발송 안 함")
        return 0

    lines = []
    for a in alerts:
        label = TYPE_LABEL.get(a.get("type"), a.get("type"))
        price = a.get("price")
        price_txt = f"{price:,}원" if isinstance(price, (int, float)) else "가격확인필요"
        per = a.get("per_100ml")
        per_txt = f" ({per}원/100ml)" if per else ""
        lines.append(f"[{label}] {a.get('product')}: {price_txt}{per_txt}")
        if a.get("url"):
            lines.append(a["url"])
    body = "\n".join(lines).encode("utf-8")
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body,
        headers={"Title": "펩시 제로 라임 특가 알림".encode("utf-8"),
                 "Tags": "tada"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        print(f"ntfy 발송 완료 (HTTP {r.status}), 알림 {len(alerts)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
