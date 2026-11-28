"""매출 분석 화면 4개를 실제로 눌러 보며 오류·메모리·표시 글자(지문) — v2 예전(표 전체) vs v2 새(DuckDB).
사이트에 쓰지 않는다. 매출 숫자는 적지 않는다(화면 글자는 지문으로만 비교, 건수만 적음).
사용: python check_v2_sales_ui.py <코드폴더> <이름> <포트> <결과json>"""
import hashlib, json, os, re, signal, subprocess, sys, threading, time


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
    code_dir, name, port, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    errf = open(f"/tmp/sui_{name}.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "v2/app.py", "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(7)
    peak, stop = [0.0], [False]

    def samp():
        while not stop[0]:
            peak[0] = max(peak[0], rss(srv.pid)); time.sleep(0.2)
    threading.Thread(target=samp, daemon=True).start()
    R = {}
    from playwright.sync_api import sync_playwright

    def idle(pg, extra=1.5, limit=600):
        t0 = time.time(); time.sleep(1)
        while time.time() - t0 < limit:
            if not pg.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(0.4)

    def snap(pg, lab, t0, note=""):
        body = pg.inner_text("main") if pg.locator("main").count() else pg.inner_text("body")
        exc = pg.locator('[data-testid="stException"]').count() + body.count("문제가 생겼습니다")
        cnt = re.findall(r"조건에 맞는 \*?\*?[\d,]+개[^\n]{0,8}|[\d,]+개 팩|매칭: [^\n]{0,60}", body)
        fp = hashlib.md5(re.sub(r"\s+", " ", body).encode()).hexdigest()[:10]
        R[lab] = {"오류": exc, "지문": fp, "건수": cnt[:3]}
        print(f"[{name}] {lab:<34} 지금 {rss(srv.pid):5.0f} MB · 최고 {peak[0]:5.0f} MB · {time.time()-t0:5.1f}초 · 오류 {exc} · {cnt[:2]} {note}", flush=True)

    def step(pg, lab, fn):
        t0 = time.time()
        try:
            fn(); snap(pg, lab, t0)
        except Exception as e:
            R[lab] = {"실패": type(e).__name__}
            print(f"[{name}] {lab:<34} 못 함 {type(e).__name__}: {str(e).splitlines()[0][:90]}", flush=True)

    def click(pg, txt):
        pg.get_by_text(txt, exact=True).first.click(); idle(pg, 2)

    def ms_pick(pg, placeholder, txt):
        pg.get_by_text(placeholder).first.click(); time.sleep(0.5)
        pg.keyboard.type(txt); time.sleep(1.2); pg.keyboard.press("Enter"); pg.keyboard.press("Escape"); idle(pg, 2)

    def sel_pick(pg, label_re, txt):
        pg.get_by_label(re.compile(label_re)).first.click(); time.sleep(0.5)
        pg.keyboard.type(txt); time.sleep(1.2); pg.keyboard.press("Enter"); idle(pg, 2)

    def combo_single(pg):
        pg.get_by_label(re.compile("구성 \\(단일제")).first.click(); time.sleep(0.5)
        pg.keyboard.press("Backspace"); pg.keyboard.press("Escape"); idle(pg, 2)

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1500, "height": 1300})
        pg.set_default_timeout(30000)
        t0 = time.time()
        pg.goto(f"http://localhost:{port}/sales", timeout=180000); idle(pg, 2)
        pg.get_by_role("button", name="📊 매출 분석 불러오기").click()
        pg.get_by_text("📊 시장 분석", exact=True).first.wait_for(timeout=900000); idle(pg, 2)
        snap(pg, "0. 매출 분석 불러옴", t0)
        for tag in ("", " · 단일제만"):
            if tag:
                step(pg, "단일제만 고름", lambda: (click(pg, "🔍 제품 대시보드"), combo_single(pg)))
            step(pg, "1. 대시보드 IQVIA 리피토정" + tag, lambda: (click(pg, "🔍 제품 대시보드"), click(pg, "IQVIA"),
                                                              ms_pick(pg, "목록에서 선택 (여러 개 가능)", "리피토정")))
            step(pg, "1-2. 대시보드 IQVIA 연간" + tag, lambda: sel_pick(pg, "집계 단위", "연간"))
            step(pg, "1-3. 대시보드 UBIST 리피토정" + tag, lambda: (click(pg, "UBIST"),
                                                               ms_pick(pg, "목록에서 선택 (여러 개 가능)", "리피토정")))
            step(pg, "2. 시장 분석 IQVIA" + tag, lambda: (click(pg, "📊 시장 분석"), click(pg, "IQVIA")))
            step(pg, "2-2. 시장 분석 UBIST" + tag, lambda: click(pg, "UBIST"))
            step(pg, "3. IQVIA vs UBIST 리피토정" + tag, lambda: (click(pg, "↔️ IQVIA vs UBIST 비교"),
                                                                 sel_pick(pg, "제품\\(브랜드\\)", "리피토정")))
            step(pg, "4. 성장 IQVIA 제품별" + tag, lambda: (click(pg, "🚀 성장 제품 탐색"), click(pg, "IQVIA")))
            step(pg, "4-2. 성장 IQVIA 성분별" + tag, lambda: sel_pick(pg, "분석 단위", "성분별"))
            step(pg, "4-3. 성장 UBIST" + tag, lambda: click(pg, "UBIST"))
        br.close()
    time.sleep(10)
    print(f"[{name}] ★ 최고 {peak[0]:.0f} MB · 끝 {rss(srv.pid):.0f} MB", flush=True)
    stop[0] = True
    R["_mem"] = {"최고": round(peak[0])}
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or ("Error" in l and "Warning" not in l)]
    print(f"[{name}] 서버 오류 줄 {len(bad)}" + ("" if not bad else ": " + " | ".join(x[:220] for x in bad[:6])))
    json.dump(R, open(out, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
