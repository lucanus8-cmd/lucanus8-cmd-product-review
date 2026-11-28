"""사이트 전체 무게: 같은 순서로 모든 화면을 눌러 보며 서버 메모리(MB)를 잰다. 사이트에 쓰지 않는다. 매출 숫자는 적지 않는다.
사용: python check_all_pages.py <old|v2> <코드폴더> <이름> <포트> <시작파일> <full|dmf|rp>"""
import os, re, signal, subprocess, sys, threading, time


def rss(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for ln in f:
                if ln.startswith("VmRSS"):
                    return int(ln.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def main():
    kind, code_dir, name, port, entry, mode = sys.argv[1:7]
    port = int(port)
    P = (lambda k: "" if k == "home" else f"?page={k}") if kind == "old" else (lambda k: "" if k == "home" else k)
    errf = open(f"/tmp/all_{name}_{mode}.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", entry, "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(7)
    peak, stop = [0.0], [False]

    def samp():
        while not stop[0]:
            peak[0] = max(peak[0], rss(srv.pid)); time.sleep(0.2)
    threading.Thread(target=samp, daemon=True).start()
    rows = []

    def rec(lab, t0, extra=""):
        now = rss(srv.pid)
        rows.append((lab, now, peak[0]))
        print(f"[{name}·{mode}] {lab:<26} 지금 {now:5.0f} MB · 최고 {peak[0]:5.0f} MB · {time.time()-t0:5.1f}초 {extra}", flush=True)

    from playwright.sync_api import sync_playwright

    def idle(pg, extra=1.5, limit=900):
        t0 = time.time(); time.sleep(1)
        while time.time() - t0 < limit:
            if not pg.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(0.4)

    def go(pg, k, q=""):
        t = time.time()
        pg.goto(f"http://localhost:{port}/{P(k)}{q}", timeout=180000); idle(pg, 2)
        while pg.get_by_text("처음 준비하고 있습니다").count() and time.time() - t < 900:
            time.sleep(3)
        idle(pg)

    def exc(pg):
        n = pg.locator('[data-testid="stException"]').count()
        return f"오류 {n}"

    def click_text(pg, txt):
        pg.get_by_text(txt, exact=True).first.click(); idle(pg, 2)

    def pick(pg, kw, opt):
        pg.get_by_label(re.compile("검색어")).first.fill(kw); pg.keyboard.press("Enter"); idle(pg, 1)
        pg.get_by_role("combobox", name=re.compile("선택")).first.click(); time.sleep(0.8)
        pg.get_by_role("option", name=re.compile(opt)).first.click(); idle(pg)

    def step(pg, lab, fn):
        t = time.time()
        try:
            fn(); rec(lab, t, exc(pg))
        except Exception as e:
            rec(lab, t, "못 함 " + type(e).__name__ + " " + str(e).splitlines()[0][:70])

    def rp(pg):
        click_text(pg, "🧬 약물 재창출")
        click_text(pg, "💊 약으로")
        pg.get_by_label(re.compile("약 이름")).first.fill("실데나필"); pg.keyboard.press("Enter"); idle(pg)
        pg.get_by_role("button", name=re.compile("지도 그리기")).first.click(); idle(pg, 3, 300)

    def sales(pg):
        go(pg, "sales")
        pg.get_by_role("button", name="📊 매출 분석 불러오기").click()
        if kind == "old":
            pg.get_by_role("tab", name=re.compile("시장 분석")).first.wait_for(timeout=1200000); idle(pg, 2)
            pg.get_by_role("tab", name=re.compile("시장 분석")).first.click(); idle(pg, 2)
        else:
            pg.get_by_text("📊 시장 분석", exact=True).first.wait_for(timeout=1200000); idle(pg, 2)
            click_text(pg, "📊 시장 분석")

    def single_only(pg):
        box = pg.get_by_label(re.compile("구성 \\(단일제"))
        box.first.click(); time.sleep(0.5); pg.keyboard.press("Backspace"); pg.keyboard.press("Escape"); idle(pg, 2)
        if not pg.get_by_text(re.compile("단일제\\*?\\*?만 분석")).count() and not pg.get_by_text("단일제만 분석합니다").count():
            body = pg.inner_text("body")
            if "만 분석합니다" not in body:
                raise RuntimeError("단일제만 안내 문구 없음")

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1500, "height": 1100})
        pg.set_default_timeout(30000)
        rec("0. 서버 켬", time.time())
        if mode == "full":
            step(pg, "1. 홈", lambda: go(pg, "home"))
            step(pg, "2. 종합분석 '리피토'", lambda: (go(pg, "search"), pick(pg, "리피토", "^리피토정10")))
            step(pg, "3. 제품 제안 (조건 필터)", lambda: go(pg, "sugg"))
            step(pg, "4. 원료사 동향(DMF)", lambda: click_text(pg, "🧪 원료사 동향(DMF)"))
            step(pg, "5. 약물 재창출 '실데나필'", lambda: rp(pg))
            step(pg, "6. 매출 분석 (시장 분석)", lambda: sales(pg))
            if kind == "v2":
                step(pg, "6-1. 매출 '단일제만'", lambda: single_only(pg))
                step(pg, "6-2. 매출 성장 제품 탐색", lambda: click_text(pg, "🚀 성장 제품 탐색"))
            step(pg, "7. 특허 분석", lambda: go(pg, "patent"))
            step(pg, "8. 임상 분석", lambda: go(pg, "clinical"))
            step(pg, "9. 약가 '로수바스타틴'", lambda: (go(pg, "price"), pick(pg, "로수바스타틴", "로수바스타틴")))
        elif mode == "dmf":
            step(pg, "1. 제품 제안 → 원료사 동향", lambda: (go(pg, "sugg", "?view=dmf"),
                                                     None if pg.get_by_text("원료사 동향 — DMF").count() else click_text(pg, "🧪 원료사 동향(DMF)")))
        elif mode == "dmfee":
            def _ee(words):
                go(pg, "sugg", "?view=dmf")
                pg.get_by_text(re.compile("질병명\\(효능·효과\\)으로 좁히기")).first.click(); time.sleep(1)
                pg.get_by_label(re.compile("질병명 \\(쉼표로")).first.fill(words); pg.keyboard.press("Enter"); idle(pg, 2)
                body = pg.inner_text("body")
                print("     ", re.findall(r"질병명으로 좁힌 성분 [\d,]+개[^\n]{0,40}|조건 맞는 성분 [\d,]+개", body)[:3],
                      "· 효능 문구 칸" + (" 있음" if "효능·효과 문구" in body else " 없음"), flush=True)
            step(pg, "1. 원료사 동향 (바로 열기)", lambda: go(pg, "sugg", "?view=dmf"))
            def _atc():
                pg.get_by_text(re.compile("ATC 계열로 좁히기")).first.click(); time.sleep(1)
                pg.get_by_text("Choose options").first.click(); time.sleep(0.6); pg.keyboard.press("Enter")
                pg.keyboard.press("Escape"); idle(pg, 2)
                print("     ", re.findall(r"ATC로 좁힌 성분 [\d,]+개", pg.inner_text("body"))[:1], flush=True)
            step(pg, "2. ATC 펼쳐서 1단계 첫 항목", _atc)
            for w in ("당뇨", "고혈압", "고지혈증, 이상지질혈증", "없는질병명zz"):
                step(pg, f"3. 질병명 '{w}'", lambda w=w: _ee(w))
        else:
            def _rp():
                go(pg, "sugg", "?view=rp")
                if not pg.get_by_text("💊 약으로", exact=True).count():
                    click_text(pg, "🧬 약물 재창출")
                rp(pg)
            step(pg, "1. 제품 제안 → 약물 재창출", _rp)
        br.close()
    time.sleep(15); rec("끝 (15초 쉬고)", time.time())
    stop[0] = True
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or ("Error" in l and "Warning" not in l)]
    print(f"[{name}·{mode}] ★ 최고 {peak[0]:.0f} MB · 끝 {rows[-1][1]:.0f} MB · 서버 오류 줄 {len(bad)}"
          + ("" if not bad else ": " + " | ".join(x[:200] for x in bad[:5])), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
