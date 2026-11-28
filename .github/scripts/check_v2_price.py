"""v2 약가 분석 vs 지금 사이트 약가 분석 — 같은 동작, 서버 메모리 비교. 사이트에는 쓰지 않는다(공개 자료만 씀).
사용: python check_v2_price.py <코드폴더> <이름> <포트> <시작파일> <약가주소>"""
import base64, json, os, re, signal, subprocess, sys, threading, time
ROOT = os.getcwd(); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))


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
    errf = open(f"/tmp/v2p_{name}.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", entry, "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(7)
    peak = [0.0]
    stop = [False]

    def samp():
        while not stop[0]:
            peak[0] = max(peak[0], rss(srv.pid)); time.sleep(0.2)
    threading.Thread(target=samp, daemon=True).start()
    rows = []

    def rec(lab, t0, ok=""):
        rows.append((lab, rss(srv.pid), peak[0]))
        print(f"[{name}] {lab:<30} 지금 {rss(srv.pid):5.0f} MB · 여기까지 최고 {peak[0]:5.0f} MB · {time.time()-t0:5.1f}초 {ok}", flush=True)

    from playwright.sync_api import sync_playwright

    def idle(pg, extra=1.5, limit=300):
        t0 = time.time(); time.sleep(1)
        while time.time() - t0 < limit:
            if not pg.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(0.4)

    def pick(pg, label_re, kw, opt_re):
        try:
            pg.get_by_label(re.compile(label_re)).first.fill(kw); pg.keyboard.press("Enter"); idle(pg, 1)
            pg.get_by_role("combobox", name=re.compile("선택")).first.click(); time.sleep(0.8)
            pg.get_by_role("option", name=re.compile(opt_re)).first.click(); idle(pg)
            return "선택됨"
        except Exception as e:
            return "못 고름(" + type(e).__name__ + ")"

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1400, "height": 1000})
        pg.set_default_timeout(30000)
        t = time.time(); rec("0. 서버 켬", t)
        t = time.time(); pg.goto(f"http://localhost:{port}/{path}", timeout=120000); idle(pg, 2, 900)
        b = pg.get_by_role("button", name="그냥 화면 열기")
        if b.count():
            b.first.click(); idle(pg)
        rec("1. 약가 분석 열기", t)
        for i, (kw, opt) in enumerate([("에독사반", "^에독사반$"), ("로수바스타틴", "^로수바스타틴"), ("아토르바스타틴", "^아토르바스타틴"),
                                       ("암로디핀", "^암로디핀$"), ("리바록사반", "^리바록사반")]):
            t = time.time(); ok = pick(pg, "성분 검색어", kw, opt); rec(f"{2+i}. 성분 그래프 '{kw}'", t, ok)
        t = time.time()
        try:
            pg.get_by_label(re.compile("^제품/성분 검색")).first.fill("리피토"); pg.keyboard.press("Enter"); idle(pg); ok = "입력"
        except Exception as e:
            ok = type(e).__name__
        rec("7. 조건 표 '리피토' (연도별 표)", t, ok)
        t = time.time()
        try:
            pg.get_by_label(re.compile("^제품/성분 검색")).first.fill("정"); pg.keyboard.press("Enter"); idle(pg); ok = "입력"
        except Exception as e:
            ok = type(e).__name__
        rec("8. 조건 표 '정' (넓은 검색)", t, ok)
        t = time.time()
        try:
            pg.get_by_text(re.compile("가격 인하폭")).first.click(); idle(pg); ok = "펼침"
        except Exception as e:
            ok = type(e).__name__
        rec("9. 인하폭·삭제 목록 펼치기", t, ok)
        exc = pg.locator('[data-testid="stException"]').count()
        br.close()
    time.sleep(10); rec("10. 10초 쉬고", time.time())
    stop[0] = True
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or "Error" in l]
    print(f"[{name}] 최고 {peak[0]:.0f} MB · 끝 {rows[-1][1]:.0f} MB · 화면 오류 {exc} · 서버 오류 줄 {len(bad)}"
          + ("" if not bad else ": " + " | ".join(x[:150] for x in bad[:4])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
