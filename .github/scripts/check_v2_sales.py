"""매출 분석: v2 vs 지금 사이트 — 같은 조작, 서버 메모리·오류. 사이트에 쓰지 않는다. 매출 숫자는 적지 않는다.
사용: python check_v2_sales.py <코드폴더> <이름> <포트> <시작파일> <매출주소>"""
import os, re, signal, subprocess, sys, threading, time

VIEWS = ["📊 시장 분석", "↔️ IQVIA vs UBIST 비교", "🚀 성장 제품 탐색", "🔍 제품 대시보드"]


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
    code_dir, name, port, entry, path = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
    errf = open(f"/tmp/v2s_{name}.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", entry, "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(7)
    peak, stop = [0.0], [False]

    def samp():
        while not stop[0]:
            peak[0] = max(peak[0], rss(srv.pid)); time.sleep(0.2)
    threading.Thread(target=samp, daemon=True).start()

    def rec(lab, t0, extra=""):
        print(f"[{name}] {lab:<34} 지금 {rss(srv.pid):5.0f} MB · 최고 {peak[0]:5.0f} MB · {time.time()-t0:5.1f}초 {extra}", flush=True)

    from playwright.sync_api import sync_playwright

    def idle(pg, extra=1.5, limit=900):
        t0 = time.time(); time.sleep(1)
        while time.time() - t0 < limit:
            if not pg.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(0.4)

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1400, "height": 1000})
        pg.set_default_timeout(30000)
        rec("0. 서버 켬", time.time())
        t = time.time(); pg.goto(f"http://localhost:{port}/{path}", timeout=180000); idle(pg, 2)
        while pg.get_by_text("처음 준비하고 있습니다").count() and time.time() - t < 900:
            time.sleep(3)
        rec("1. 매출 분석 화면", t)
        t = time.time()
        pg.get_by_role("button", name=re.compile("매출 분석 불러오기")).click()
        pg.get_by_text(VIEWS[1], exact=True).first.wait_for(timeout=900000); idle(pg, 2)
        rec("2. 불러오기 (제품 대시보드)", t, f"오류 {pg.locator('[data-testid=stException]').count()}")
        for rnd in (1, 2):
            for v in VIEWS:
                t = time.time()
                try:
                    pg.get_by_text(v, exact=True).first.click(); idle(pg)
                    ok = f"오류 {pg.locator('[data-testid=stException]').count()}"
                except Exception as e:
                    ok = "못 누름 " + type(e).__name__
                rec(f"{2+rnd}회째 {v}", t, ok)
        br.close()
    time.sleep(8); rec("끝 (8초 쉬고)", time.time())
    stop[0] = True
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or "Error:" in l]
    print(f"[{name}] 최고 {peak[0]:.0f} MB · 서버 오류 줄 {len(bad)}" + ("" if not bad else ": " + " | ".join(x[:160] for x in bad[:4])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
