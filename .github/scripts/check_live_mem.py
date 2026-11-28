"""실제 운영 중인 v2 사이트 메모리 읽기 — 상태 화면의 '서버 메모리 지금 N MB'만 적는다(사이트에 쓰지 않음, 자료 글자는 적지 않음).
처음 → 화면 몇 개를 차례로 연 뒤 → 다시 읽는다. 사람이 쓰는 것과 같은 정도의 부담."""
import os, re, sys, time
from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""


def frame(pg):
    for fr in pg.frames:
        try:
            if fr.query_selector("[data-testid='stAppViewContainer']"):
                return fr
        except Exception:
            pass
    return pg.main_frame


def idle(fr, extra=2, limit=300):
    t0 = time.time(); time.sleep(1.5)
    while time.time() - t0 < limit:
        try:
            if not fr.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not fr.locator('[data-testid="stStatusWidget"]').count():
                    return
        except Exception:
            pass
        time.sleep(0.5)


def main():
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_context(viewport={"width": 1500, "height": 1300}, locale="ko-KR").new_page()
        pg.goto(URL + "/status", wait_until="domcontentloaded", timeout=180_000)
        t0 = time.time()
        while time.time() - t0 < 240:           # 잠들어 있으면 깨우기
            for fr in pg.frames:
                try:
                    b = fr.locator("button").filter(has_text="back up").first
                    if b.count():
                        b.click(); print("잠들어 있어 깨움"); time.sleep(20)
                except Exception:
                    pass
            fr = frame(pg)
            if fr.query_selector("input[type='password']") or "서버 메모리" in (fr.inner_text("body") if fr else ""):
                break
            time.sleep(4)
        fr = frame(pg)
        if PW and fr.query_selector("input[type='password']"):
            fr.fill("input[type='password']", PW); fr.press("input[type='password']", "Enter"); time.sleep(8)
            print("비밀번호 넣음")
        fr = frame(pg); idle(fr)

        def read(lab):
            f = frame(pg)
            f.get_by_role("link", name=re.compile("상태")).first.click(); idle(f)
            m = re.search(r"서버 메모리 지금 ([\d,]+) MB", f.inner_text("body"))
            b = re.search(r"v2-\d{4}-\d{2}-\d{2}-\w", f.inner_text("body"))
            print(f"★ {lab}: 서버 메모리 {m.group(1) if m else '?'} MB" + (f" · 버전 {b.group(0)}" if b else ""), flush=True)

        read("처음 (지금 상태)")
        for name, extra in [("제품 종합분석", None), ("제품 제안", None), ("특허 분석", None), ("임상 분석", None),
                            ("약가 분석", None), ("매출 분석", "📊 매출 분석 불러오기")]:
            f = frame(pg)
            try:
                f.get_by_role("link", name=re.compile(name)).first.click(); idle(f, 3, 600)
                if extra:
                    f.get_by_role("button", name=extra).first.click(); idle(f, 3, 900)
                print(f"   {name} 열음 · 오류 {f.locator('[data-testid=stException]').count()}", flush=True)
            except Exception as e:
                print(f"   {name} 못 엶: {type(e).__name__}", flush=True)
        read("화면 6개를 열어 본 뒤")
        time.sleep(30)
        read("30초 쉬고")
        br.close()


if __name__ == "__main__":
    sys.exit(main())
