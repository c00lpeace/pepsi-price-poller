#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
펩시 제로 라임 가격 폴러
- 쿠팡 상품 페이지에 HTTP GET 1회 -> HTML 안의 JSON-LD(application/ld+json)에서 가격 파싱
- 로그인 / API 키 불필요, 파이썬 표준 라이브러리만 사용 (별도 설치 없음)
- 실행: python3 poll.py
- 결과: prices.jsonl 에 1줄 추가, 특가 감지 시 alert.json 갱신
- GitHub Actions / 로컬 PC 어디서든 실행 가능
"""
import json
import re
import time
import urllib.request
from datetime import datetime, timezone

# ---------------- 설정 ----------------
PRODUCTS = [
    {
        "id": "pepsi-zero-lime-1.5lx12",
        "name": "펩시 제로슈거 라임향 1.5L 12개",
        "url": "https://www.coupang.com/vp/products/6384738608?itemId=21534128269&vendorItemId=88263917206",
        "volume_ml": 1500 * 12,
        # 알림 기준
        "alert_below_per_100ml": 95.0,  # 100ml당 이 가격 이하 -> 최저가 경신급 특가
        "alert_drop_pct": 5.0,          # 직전 가격 대비 이 % 이상 하락 -> 급락
    },
    # 상품 추가 예시:
    # {
    #     "id": "pepsi-zero-lime-355mlx24",
    #     "name": "펩시 제로슈거 라임향 355ml 24캔",
    #     "url": "https://www.coupang.com/vp/products/<상품번호>?itemId=<itemId>&vendorItemId=<vendorItemId>",
    #     "volume_ml": 355 * 24,
    #     "alert_below_per_100ml": 95.0,
    #     "alert_drop_pct": 5.0,
    # },
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HISTORY_FILE = "prices.jsonl"
ALERT_FILE = "alert.json"
RETRIES = 3  # 빈 페이지(봇 탐지) 대응 재시도 횟수


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch(url):
    """상품 페이지 HTML 가져오기. 실패/빈 페이지면 재시도."""
    last_err = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Accept": "text/html",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
                if len(body) < 50000 or "ld+json" not in body:
                    # 쿠팡 봇 탐지가 간헐적으로 빈 페이지를 반환함
                    last_err = f"blank_or_too_small(len={len(body)})"
                    time.sleep(5 * (i + 1))
                    continue
                return body, None
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(5 * (i + 1))
    return None, last_err


def parse_price(html):
    """JSON-LD 블록에서 상품명·가격·재고상태 추출."""
    pattern = re.compile(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        re.S | re.I,
    )
    for m in pattern.finditer(html):
        try:
            data = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            if not offers:
                continue
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price")
            if not price:
                continue
            avail = str(offers.get("availability", ""))
            if "OutOfStock" in avail:
                availability = "out_of_stock"
            elif "InStock" in avail:
                availability = "in_stock"
            else:
                availability = "unknown"
            try:
                price_int = int(float(price))
            except (ValueError, TypeError):
                continue
            return {
                "name": item.get("name"),
                "price": price_int,
                "availability": availability,
            }
    return None


def last_ok_record(product_id):
    """히스토리에서 해당 상품의 마지막 정상 기록."""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if rec.get("id") == product_id and rec.get("status") == "ok":
            return rec
    return None


def main():
    alerts = []
    for p in PRODUCTS:
        body, err = fetch(p["url"])
        rec = {
            "ts": now_iso(),
            "id": p["id"],
            "name": p["name"],
            "url": p["url"],
        }
        parsed = parse_price(body) if body else None
        if not parsed:
            rec["status"] = "blocked" if body is None or err else "parse_error"
            rec["error"] = err or "price not found in HTML"
            print(f"[차단/실패] {p['name']}: {rec['error']}")
        else:
            per_100ml = round(parsed["price"] / p["volume_ml"] * 100, 2)
            rec.update({
                "status": "ok",
                "price": parsed["price"],
                "per_100ml": per_100ml,
                "availability": parsed["availability"],
            })
            print(f"[확인] {p['name']}: {parsed['price']:,}원 "
                  f"({per_100ml}원/100ml, {parsed['availability']})")

            prev = last_ok_record(p["id"])
            # 1) 최저가 경신급
            if per_100ml <= p["alert_below_per_100ml"]:
                alerts.append({"type": "new_low", **alert_base(p, rec)})
            # 2) 직전 대비 급락
            elif prev and prev.get("price"):
                drop = (prev["price"] - parsed["price"]) / prev["price"] * 100
                if drop >= p["alert_drop_pct"]:
                    a = alert_base(p, rec)
                    a["drop_pct"] = round(drop, 1)
                    a["prev_price"] = prev["price"]
                    alerts.append({"type": "sudden_drop", **a})
            # 3) 재고 상태 변화
            if prev and prev.get("availability") != parsed["availability"]:
                if parsed["availability"] == "out_of_stock":
                    alerts.append({"type": "out_of_stock", **alert_base(p, rec)})
                elif prev.get("availability") == "out_of_stock":
                    alerts.append({"type": "back_in_stock", **alert_base(p, rec)})

        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        json.dump({"checked_at": now_iso(), "alerts": alerts},
                  f, ensure_ascii=False, indent=2)
    print(f"완료: 특가 알림 {len(alerts)}건")
    return 0


def alert_base(p, rec):
    return {
        "product": p["name"],
        "price": rec.get("price"),
        "per_100ml": rec.get("per_100ml"),
        "url": p["url"],
        "ts": rec["ts"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
