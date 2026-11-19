"""배포된 사이트에 'FDA 비교' 보기가 올라갔고 오류 없이 도는지 확인한다. 읽기만 한다."""
import os
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""


def frame(page):
    for fr in page.frames:
        try:
            if fr.query_selector("[data-testid='stApp'], [data-testid='stAppViewContainer'], input[type='password']"):
                return fr
        except Exception:
            pass
    return None


def text(fr):
    try:
        return fr.inner_text("body")
    except Exception:
        return ""


def errors(fr):
    try:
        return len(fr.query_selector_all("[data-testid='stException']"))
    except Exception:
        return -1


def wait_text(fr, t, sec=90):
    for _ in range(sec // 2):
        if t in text(fr):
            return True
        time.sleep(2)
    return False


def main():
    ok = True
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_context(viewport={"width": 1500, "height": 2200}, locale="ko-KR").new_page()
        pg.goto(URL + "/?page=status", wait_until="domcontentloaded", timeout=180_000)
        fr = None
        for _ in range(90):
            fr = frame(pg)
            if fr:
                break
            time.sleep(2)
        if not fr:
            print("앱 화면을 못 찾음 (깨우는 중일 수 있음)")
            return 1
        time.sleep(5)
        pw_in = fr.query_selector("input[type='password']")
        if pw_in and PW:
            pw_in.fill(PW)
            pw_in.press("Enter")
            time.sleep(8)
        fr = frame(pg)
        got = wait_text(fr, "코드 버전", 120)
        t = text(fr)
        ver = next((ln for ln in t.splitlines() if "코드 버전" in ln), "")
        print("상태 화면:", ver or "코드 버전 줄 못 찾음")
        if "2026-11-19-c" not in t:
            print("❌ 새 코드(2026-11-19-c)가 아직 안 올라감")
            ok = False
        btn = fr.get_by_role("button", name="제품 제안")
        btn.first.click()
        time.sleep(8)
        fr = frame(pg)
        fr.get_by_text("FDA 비교", exact=True).first.click()
        if not wait_text(fr, "① 성분으로 찾기", 90):
            print("❌ FDA 비교 보기가 안 뜸")
            print(text(fr)[-600:])
            return 1
        cap = next((ln for ln in text(fr).splitlines() if "FDA 허가" in ln and "라벨" in ln), "")
        print("✅ FDA 비교 열림 ·", cap[:160])
        box = fr.get_by_label("성분·상표 (한글·영문)").first
        box.fill("리바록사반")
        box.press("Enter")
        ok &= wait_text(fr, "일부 제형 없음", 60)
        print("① 리바록사반:", "✅" if "XARELTO" in text(fr) else "❌", "· 오류", errors(fr))
        fr.get_by_text("② 적응증으로 찾기").first.click()
        time.sleep(5)
        box = fr.get_by_label("적응증 (한글·영문)").first
        box.fill("폐동맥고혈압")
        box.press("Enter")
        ok &= wait_text(fr, "이 적응증 미국 판매 성분", 60)
        print("② 폐동맥고혈압:", "✅" if "적응증 문장에서" in text(fr) else "❌ (" + text(fr)[-200:] + ")", "· 오류", errors(fr))
        ok &= "적응증 문장에서" in text(fr)
        box.fill("이상지질혈증")
        box.press("Enter")
        time.sleep(8)
        print("② 이상지질혈증:", next((ln for ln in text(fr).splitlines() if "적응증 문장에서" in ln), "")[:120],
              "· 오류", errors(fr))
        fr.get_by_text("③ 국내에 없는 약 목록").first.click()
        ok &= wait_text(fr, "미국 첫 허가가 최근인 순", 60)
        print("③ 목록:", "✅" if "국내 성분 없음 (" in text(fr) else "❌", "· 오류", errors(fr))
        pg.screenshot(path="fda_live.png")
        for p_ in ("patent", "search"):
            pg.goto(URL + f"/?page={p_}", wait_until="domcontentloaded", timeout=120_000)
            fr = None
            for _ in range(45):
                fr = frame(pg)
                if fr:
                    break
                time.sleep(2)
            time.sleep(5)
            if fr and fr.query_selector("input[type='password']") and PW:
                fr.query_selector("input[type='password']").fill(PW)
                fr.query_selector("input[type='password']").press("Enter")
                time.sleep(15)
                fr = frame(pg)
            e = errors(fr) if fr else -1
            print(f"   {p_}: 화면 글자 {len(text(fr)) if fr else 0}자")
            print(f"{p_} 화면 오류 {e}")
            ok &= e == 0
        br.close()
    print("결과:", "정상" if ok else "확인 필요")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
