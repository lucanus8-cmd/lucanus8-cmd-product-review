"""특허·임상 분석: v2 vs 지금 사이트 — 같은 조작, 서버 메모리·결과 건수. 사이트에 쓰지 않는다(공개 자료만).
사용: python check_v2_patclin.py <코드폴더> <이름> <포트> <시작파일> <특허주소> <임상주소>"""
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
    code_dir, name, port, entry, p_pat, p_cli = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
    errf = open(f"/tmp/v2pc_{name}.err", "w+")
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
        print(f"[{name}] {lab:<30} 지금 {rss(srv.pid):5.0f} MB · 최고 {peak[0]:5.0f} MB · {time.time()-t0:5.1f}초 {extra}", flush=True)

    from playwright.sync_api import sync_playwright

    def idle(pg, extra=1.5, limit=900):
        t0 = time.time(); time.sleep(1)
        while time.time() - t0 < limit:
            if not pg.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(0.4)

    def num(pg, lab):
        m = re.search(lab + r"\s*\n+\s*([\d,]+)", pg.inner_text("body"))
        return m.group(1) if m else "?"

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1400, "height": 1000})
        pg.set_default_timeout(30000)
        rec("0. 서버 켬", time.time())
        t = time.time(); pg.goto(f"http://localhost:{port}/{p_pat}", timeout=180000); idle(pg, 2)
        while pg.get_by_text("처음 준비하고 있습니다").count() and time.time() - t < 900:
            time.sleep(3)
        idle(pg)
        rec("1. 특허 분석 (기본)", t, f"건수 {num(pg, '특허 건수')} · 특허권자 {num(pg, '특허권자 수')} · 3년내 {num(pg, '향후 3년 내 만료')} · 오류 {pg.locator('[data-testid=stException]').count()}")
        for kw in ["리바록사반", "rosuvastatin", "에제티미브"]:
            t = time.time()
            pg.get_by_label(re.compile("품목/성분 검색")).fill(kw); pg.keyboard.press("Enter"); idle(pg)
            rec(f"특허 '{kw}'", t, f"건수 {num(pg, '특허 건수')} · 특허권자 {num(pg, '특허권자 수')} · 오류 {pg.locator('[data-testid=stException]').count()}")
        t = time.time(); pg.goto(f"http://localhost:{port}/{p_cli}", timeout=180000); idle(pg, 2)
        rec("2. 임상 분석 (검색)", t, f"오류 {pg.locator('[data-testid=stException]').count()}")
        for v in ["📊 분석", "🧮 생동 피험자 수", "💰 1상 비용 어림", "📐 간이 메타분석", "🔍 검색"]:
            t = time.time()
            try:
                pg.locator("label").filter(has_text=v).first.click(); idle(pg)
                ok = f"오류 {pg.locator('[data-testid=stException]').count()}"
            except Exception as e:
                ok = "못 누름 " + type(e).__name__
            rec(f"임상 {v}", t, ok)
        br.close()
    time.sleep(8); rec("끝 (8초 쉬고)", time.time())
    stop[0] = True
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or "Error:" in l]
    print(f"[{name}] 최고 {peak[0]:.0f} MB · 서버 오류 줄 {len(bad)}" + ("" if not bad else ": " + " | ".join(x[:160] for x in bad[:4])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
