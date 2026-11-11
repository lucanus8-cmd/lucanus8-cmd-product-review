"""배포된 사이트에 '새 코드'가 실제로 올라갔는지 확인한다.

화면이 잘 뜨는 것만으로는 부족하다 — 옛 코드도 잘 떴다. 이번에 새로 생긴
칸이 눈에 보이는지로 판별한다. 읽기만 하고 사이트는 바꾸지 않는다.
"""
import os
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
APP_SEL = ("[data-testid='stApp']", "[data-testid='stAppViewContainer']",
           "[data-testid='stMain']", "input[type='password']", "section.main")

# (찾을 글자, 설명, 없으면 실패로 볼 것인가)
MARKS = [
    ("④ 자사 동일가", "유형 ④ 자사 동일가", True),
    ("⑤ 함량배수", "유형 ⑤ 함량배수", True),
    ("오리지널의 인하 이력 보기", "오리지널 인하 이력 체크 칸", True),
    ("오리지널 조정 상태", "조정 상태 라디오 셋", True),
    ("① 단일제 자료제출", "유형 ① (전부터 있던 칸)", False),
]
# ⑤ 를 눌렀을 때만 보이는 것
MARKS_DOSE = [
    ("제품 검색", "⑤ 제품명으로 찾는 칸", True),
    ("비교 제품 선택", "⑤ 비교 제품 고르는 칸", True),
    ("비교 제품의 상한금액", "⑤ A 의 기준 칸", True),
    ("신청 제품 함량", "⑤ 신청 함량 칸", True),
]


def find_all(page, sel):
    out = []
    for fr in page.frames:
        try:
            out += fr.query_selector_all(sel)
        except Exception:
            pass
    return out


def text_of(page):
    parts = []
    for fr in page.frames:
        try:
            t = fr.inner_text("body")
        except Exception:
            continue
        if t:
            parts.append(t)
    return "\n".join(parts)


def app_frame(page):
    for fr in page.frames:
        for sel in APP_SEL:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return None


def main():
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_context(viewport={"width": 1500, "height": 1600},
                            locale="ko-KR").new_page()
        pg.goto(URL + "/?page=price", wait_until="domcontentloaded", timeout=120_000)
        for _ in range(60):
            if app_frame(pg):
                break
            time.sleep(2)
        if not app_frame(pg):
            print("앱 화면을 못 찾았다 — 판정 불가")
            br.close()
            return 1
        time.sleep(8)

        logged = False
        for _ in range(60):
            if not logged and PW:
                box = None
                got = find_all(pg, "input[type='password']")
                box = got[0] if got else None
                if box:
                    box.fill(PW)
                    box.press("Enter")
                    logged = True
                    print("로그인했다")
                    time.sleep(14)
                    continue
            if "약가 분석" in (text_of(pg) or ""):
                break
            time.sleep(2)

        # '약가 산정' 으로 전환
        hit = False
        for lb in find_all(pg, "label"):
            try:
                if "약가 산정" in (lb.inner_text() or ""):
                    lb.click()
                    hit = True
                    break
            except Exception:
                pass
        if not hit:
            print("'약가 산정' 칸을 못 찾았다 — 판정 불가")
            br.close()
            return 1
        time.sleep(22)

        body = text_of(pg) or ""

        # ⑤ 함량배수까지 눌러 본다(비교 제품 칸은 그때만 생긴다)
        for lb in find_all(pg, "label"):
            try:
                if "⑤ 함량배수" in (lb.inner_text() or ""):
                    lb.click()
                    # 실제 사이트는 2만 품목 목록을 만드느라 오래 걸린다. 고정
                    # 시간으로 기다리면 다 그려지기 전에 '없다'고 판정한다.
                    for _ in range(18):
                        time.sleep(5)
                        if "신청 제품 함량" in (text_of(pg) or ""):
                            break
                    break
            except Exception:
                pass
        body += "\n" + (text_of(pg) or "")

        ex = find_all(pg, "[data-testid='stException'], .stException")
        print(f"\n예외 {len(ex)}건")
        if ex:
            print((ex[0].inner_text() or "")[:400])

        print("\n새 칸이 보이는지")
        missing_new = 0
        for word, desc, is_new in MARKS + MARKS_DOSE:
            ok = word in body
            if is_new and not ok:
                missing_new += 1
            tag = "새것" if is_new else "기존"
            print(f"   [{'보임' if ok else '안보임'}] {tag} · {desc}")

        print()
        if ex:
            print("화면에 예외가 떠 있다 — 확인이 필요하다")
            br.close()
            return 1
        if missing_new:
            print(f"새 칸 {missing_new}개가 안 보인다 — 옛 코드가 돌고 있을 수 있다")
            for hint in ("고른 제품의 성분 묶음을 찾지 못했습니다",
                         "조건에 맞는 제품이 없습니다",
                         "약가 자료를 읽지 못했습니다"):
                if hint in body:
                    print(f"  >>> 화면에 이 말이 떠 있다: {hint}")
            print("그때 화면 앞부분:", body[:400].replace("\n", " | "))
            br.close()
            return 1
        print("새 코드가 올라갔고, 화면에 예외가 없다")
        br.close()
        return 0


raise SystemExit(main())
