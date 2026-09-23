#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
펩시 제로 라임 가격 폴러 (Playwright 버전)
- 실제 설치된 Chrome을 화면 있는 모드로 띄워 상품 페이지를 열기 때문에
  쿠팡 봇 탐지에 걸릴 확률이 낮음 (urllib 버전이 TLS 지문으로 막힐 때 쓰는 대안)
- USE_PROFILE_COPY=True 이면 실제 크롬 프로필(쿠팡 쿠키 포함)을 복사해
  persistent context로 실행 → Akamai가 "돌아오는 사용자"로 인식
- 설치: pip install playwright && playwright install chromium
- 실행: python poll_playwright.py
- 결과: prices.jsonl 에 1줄 추가, 특가 감지 시 alert.json 갱신
"""
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

# ---------------- 브라우저 실행 설정 ----------------
# 쿠팡 봇 탐지를 피하기 위한 설정.
# - 새 프로필(쿠키·방문 이력 없음)은 Akamai가 "처음 보는 브라우저"로 분류해 차단
# - 실제 크롬 프로필(쿠팡 쿠키 포함)을 복사해 쓰면 "돌아오는 사용자"로 인식될 확률이 높음
USE_REAL_CHROME = True  # 설치된 Google Chrome 사용 (없으면 기본 Chromium으로 자동 전환)
HEADLESS = False        # False = 화면 있는 모드 (탐지 회피에 유리, 실행 시 창이 잠깐 뜸)
USE_PROFILE_COPY = True  # True = 실제 크롬 프로필을 복사해 persistent context로 실행

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_PROFILE_DIR = os.path.join(SCRIPT_DIR, "chrome-profile")
REAL_PROFILE_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")


def _copytree_best_effort(src, dst):
    """파일 잠금 등으로 실패하는 파일은 건너뛰고 최대한 복사. (복사, 건너뜀) 개수 반환."""
    copied, skipped = 0, 0
    skip_dirs = {"Cache", "Code Cache", "GPUCache", "Service Worker",
                 "ShaderCache", "DawnGraphiteCache", "DawnWebGPUCache"}
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rel = os.path.relpath(root, src)
        target_root = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            target = os.path.join(target_root, name)
            if os.path.exists(target):
                continue
            try:
                shutil.copy2(os.path.join(root, name), target)
                copied += 1
            except Exception:  # noqa: BLE001
                skipped += 1
    return copied, skipped


def prepare_profile():
    """실제 크롬 프로필(Default)을 로컬에 복사. 이미 복사본이 있으면 재사용."""
    default_dst = os.path.join(LOCAL_PROFILE_DIR, "Default")
    if os.path.isdir(default_dst):
        print("기존 프로필 복사본을 재사용합니다.")
        return LOCAL_PROFILE_DIR
    default_src = os.path.join(REAL_PROFILE_DIR, "Default")
    if not os.path.isdir(default_src):
        print("실제 크롬 프로필을 찾지 못했습니다. 새 프로필 모드로 실행합니다.")
        return None
    print("크롬 프로필을 복사합니다...")
    print("TIP: 크롬을 완전히 종료한 상태에서 실행하면 쿠키가 온전히 복사됩니다.")
    copied, skipped = _copytree_best_effort(default_src, default_dst)
    try:
        ls_src = os.path.join(REAL_PROFILE_DIR, "Local State")
        ls_dst = os.path.join(LOCAL_PROFILE_DIR, "Local State")
        if os.path.exists(ls_src) and not os.path.exists(ls_dst):
            shutil.copy2(ls_src, ls_dst)
    except Exception:  # noqa: BLE001
        pass
    print(f"프로필 복사 완료: {copied}개 복사, {skipped}개 건너뜀")
    return LOCAL_PROFILE_DIR


def start_session(pw):
    """(page, close_fn) 반환. 프로필 모드 우선, 실패 시 headed 새 프로필로 폴백."""
    args = ["--disable-blink-features=AutomationControlled"]
    if USE_PROFILE_COPY:
        profile_dir = prepare_profile()
        if profile_dir:
            try:
                kwargs = {
                    "user_data_dir": profile_dir,
                    "headless": HEADLESS,
                    "args": args,
                    "locale": "ko-KR",
                    # user_agent를 지정하지 않음: 실제 크롬의 자연스러운 값을 그대로 사용
                }
                if USE_REAL_CHROME:
                    kwargs["channel"] = "chrome"
                context = pw.chromium.launch_persistent_context(**kwargs)
                print("실제 크롬 프로필(쿠키 포함)로 실행합니다.")
                return context.new_page(), context.close
            except Exception as e:  # noqa: BLE001
                print(f"프로필 모드 실패, 일반 모드로 전환: {e}")
    # 폴백: 새 프로필 headed 모드
    if USE_REAL_CHROME:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=HEADLESS, args=args)
            return browser.new_page(user_agent=UA, locale="ko-KR"), browser.close
        except Exception as e:  # noqa: BLE001
            print(f"실제 크롬 실행 실패, 기본 Chromium으로 전환: {e}")
    browser = pw.chromium.launch(headless=HEADLESS, args=args)
    return browser.new_page(user_agent=UA, locale="ko-KR"), browser.close


# ---------------- 상품 설정 ----------------
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
    print("브라우저 시작 중... (크롬 창이 잠깐 열립니다)")
    with sync_playwright() as pw:
        page, close_session = start_session(pw)
        try:
            for p in PRODUCTS:
                rec, prod_alerts = check_product(page, p)
                alerts.extend(prod_alerts)
                with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        finally:
            close_session()

    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        json.dump({"checked_at": now_iso(), "alerts": alerts},
                  f, ensure_ascii=False, indent=2)
    print(f"완료: 특가 알림 {len(alerts)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
