#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
펩시 제로 라임 가격 폴러 (Playwright 버전)
- headless Chromium(진짜 브라우저)으로 상품 페이지를 열어 HTML 수집
- urllib 버전(poll.py)이 쿠팡의 TLS 지문 탐지에 막힐 때 쓰는 대안
- 설치: pip install playwright && playwright install chromium
- 실행: python poll_playwright.py
- 결과: prices.jsonl 에 1줄 추가, 특가 감지 시 alert.json 갱신
"""
import json
import re
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

# ---------------- 설정 ----------------
PRODUCTS = [
    {
        "id": "pepsi-zero-lime-1.5lx12",
        "name": "펩시 제로슈거 라임향 1.5L 12개",
        "url": "https://www.coupang.com/vp/products/6384738608?itemId=21534128269&vendorItemId=88263917206",
        "volume_ml": 1500 * 12,
        # 알림 기준
        "alert_below_per_100ml": 95.0,  # 100ml당 이 가격 이하 -> 최저가 경신급
        "alert_drop_pct": 5.0,          # 직전 가격 대비 이 % 이상 하락 -> 급락
    },
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HISTORY_FILE = "prices.jsonl"
ALERT_FILE = "alert.json"
RETRIES = 3
NAV_TIMEOUT_MS = 25000


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_page(page, url):
    """Playwright 페이지로 상품 HTML 가져오기. 실패하면 재시도."""
    last_err = None
    for i in range(RETRIES):
        try:
            resp = page.goto(url, wait_until="domcontentloaded",
                             timeout=NAV_TIMEOUT_MS)
            if resp is None:
                last_err = "no response"
            elif resp.status >= 400:
                last_err = f"HTTP {resp.status}"
            else:
                body = page.content()
                if len(body) < 50000 or "ld+json" not in body:
                    last_err = f"blank_or_too_small(len={len(body)})"
                else:
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


def alert_base(p, rec):
    return {
        "product": p["name"],
        "price": rec.get("price"),
        "per_100ml": rec.get("per_100ml"),
        "url": p["url"],
        "ts": rec["ts"],
    }


def check_product(page, p):
    """한 상품 수집 → (record, alerts) 반환."""
    body, err = fetch_page(page, p["url"])
    rec = {"ts": now_iso(), "id": p["id"], "name": p["name"], "url": p["url"]}
    alerts = []
    parsed = parse_price(body) if body else None
    if not parsed:
        rec["status"] = "blocked" if body is None else "parse_error"
        rec["error"] = err or "price not found in HTML"
        print(f"[차단/실패] {p['name']}: {rec['error']}")
        return rec, alerts

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
    if per_100ml <= p["alert_below_per_100ml"]:
        alerts.append({"type": "new_low", **alert_base(p, rec)})
    elif prev and prev.get("price"):
        drop = (prev["price"] - parsed["price"]) / prev["price"] * 100
        if drop >= p["alert_drop_pct"]:
            a = alert_base(p, rec)
            a["drop_pct"] = round(drop, 1)
            a["prev_price"] = prev["price"]
            alerts.append({"type": "sudden_drop", **a})
    if prev and prev.get("availability") != parsed["availability"]:
        if parsed["availability"] == "out_of_stock":
            alerts.append({"type": "out_of_stock", **alert_base(p, rec)})
        elif prev.get("availability") == "out_of_stock":
            alerts.append({"type": "back_in_stock", **alert_base(p, rec)})
    return rec, alerts


def main():
    alerts = []
    print("브라우저 시작 중...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA, locale="ko-KR")
        try:
            for p in PRODUCTS:
                rec, prod_alerts = check_product(page, p)
                alerts.extend(prod_alerts)
                with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        finally:
            browser.close()

    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        json.dump({"checked_at": now_iso(), "alerts": alerts},
                  f, ensure_ascii=False, indent=2)
    print(f"완료: 특가 알림 {len(alerts)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
