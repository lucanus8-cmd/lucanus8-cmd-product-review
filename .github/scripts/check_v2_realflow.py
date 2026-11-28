"""실제 사용 순서 그대로: 자료가 하나도 없는 상태에서 v2 를 켜고 → 종합분석(자료 처음 받기) → 제품 제안 →
매출 '불러오기' → 매출 화면 → 특허·임상·약가. 사이트 본체 메모리와 별도 작업(자식)까지 합친 메모리를 잰다.
사이트(Streamlit Cloud)에는 쓰지 않는다. 매출 숫자는 적지 않는다.
사용: python check_v2_realflow.py <코드폴더> <이름> <포트>"""
import os, re, shutil, signal, subprocess, sys, threading, time


def rss(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for ln in f:
                if ln.startswith("VmRSS"):
                    return int(ln.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def tree(pid):
    kids = []
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
        kids = [int(x) for x in out]
        for k in list(kids):
            kids += [int(x) for x in subprocess.run(["pgrep", "-P", str(k)], capture_output=True, text=True).stdout.split()]
    except Exception:
        pass
    me = rss(pid)
    return me, me + sum(rss(k) for k in kids)


def main():
    code_dir, name, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    for d in ("scout_data", "saved_data"):           # 빈 상태에서 시작
        shutil.rmtree(os.path.join(code_dir, d), ignore_errors=True)
    errf = open(f"/tmp/rf_{name}.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "v2/app.py", "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(7)
    pk = {"me": 0.0, "all": 0.0}
    stop = [False]

    def samp():
        while not stop[0]:
            a, b = tree(srv.pid)
            pk["me"] = max(pk["me"], a); pk["all"] = max(pk["all"], b); time.sleep(0.3)
    threading.Thread(target=samp, daemon=True).start()
    from playwright.sync_api import sync_playwright

    def idle(pg, extra=2, limit=1500):
        t0 = time.time(); time.sleep(1.5)
        while time.time() - t0 < limit:
            busy = pg.locator('[data-testid="stStatusWidget"]').count() or pg.get_by_text(re.compile("받는 중|처음 준비|불러오는 중|준비 중")).count()
            if not busy:
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(1)

    def rec(lab, t0, note=""):
        a, b = tree(srv.pid)
        print(f"[{name}] {lab:<26} 본체 {a:5.0f} MB (최고 {pk['me']:5.0f}) · 자식 포함 최고 {pk['all']:5.0f} MB · {time.time()-t0:6.1f}초 {note}", flush=True)

    def step(pg, lab, fn):
        t0 = time.time()
        try:
            fn(); rec(lab, t0, f"오류 {pg.locator('[data-testid=stException]').count()}")
        except Exception as e:
            rec(lab, t0, "못 함 " + type(e).__name__ + " " + str(e).splitlines()[0][:80])

    def go(pg, k):
        pg.goto(f"http://localhost:{port}/{k}", timeout=300000); idle(pg)

    def pick(pg, kw, opt):
        pg.get_by_label(re.compile("검색어")).first.fill(kw); pg.keyboard.press("Enter"); idle(pg, 1)
        pg.get_by_role("combobox", name=re.compile("선택")).first.click(); time.sleep(0.8)
        pg.get_by_role("option", name=re.compile(opt)).first.click(); idle(pg)

    def sales(pg):
        go(pg, "sales")
        pg.get_by_role("button", name="📊 매출 분석 불러오기").click()
        pg.get_by_text("📊 시장 분석", exact=True).first.wait_for(timeout=1500000); idle(pg)
        pg.get_by_text("📊 시장 분석", exact=True).first.click(); idle(pg)

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1500, "height": 1100})
        pg.set_default_timeout(60000)
        rec("0. 서버 켬 (자료 없음)", time.time())
        step(pg, "1. 홈", lambda: go(pg, ""))
        step(pg, "2. 종합분석 (자료 처음 받기)", lambda: (go(pg, "search"), pick(pg, "리피토", "^리피토정10")))
        step(pg, "3. 제품 제안", lambda: go(pg, "sugg"))
        step(pg, "4. 매출 불러오기 → 시장 분석", lambda: sales(pg))
        step(pg, "5. 매출 성장 제품 탐색", lambda: (pg.get_by_text("🚀 성장 제품 탐색", exact=True).first.click(), idle(pg)))
        step(pg, "6. 특허 분석", lambda: go(pg, "patent"))
        step(pg, "7. 임상 분석", lambda: go(pg, "clinical"))
        step(pg, "8. 약가 '로수바스타틴'", lambda: (go(pg, "price"), pick(pg, "로수바스타틴", "로수바스타틴")))
        br.close()
    time.sleep(20); rec("9. 20초 쉬고", time.time())
    stop[0] = True
    a, _ = tree(srv.pid)
    print(f"[{name}] ★ 본체 최고 {pk['me']:.0f} MB · 자식 포함 최고 {pk['all']:.0f} MB · 끝(본체) {a:.0f} MB", flush=True)
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or ("Error" in l and "Warning" not in l)]
    print(f"[{name}] 서버 오류 줄 {len(bad)}" + ("" if not bad else ": " + " | ".join(x[:200] for x in bad[:5])), flush=True)


if __name__ == "__main__":
    main()
