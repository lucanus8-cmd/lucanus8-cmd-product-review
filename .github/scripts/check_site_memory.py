"""지금 사이트(main)와 가벼운 버전(작업 가지)을 로봇에서 실제로 띄워, 같은 순서로 화면을 눌러 보며
서버 메모리를 잰다. 사이트(Streamlit Cloud)에는 아무것도 쓰지 않는다. 매출 숫자는 적지 않고 MB만 적는다.

사용: python check_site_memory.py <코드폴더> <이름> <포트>
"""
import base64, json, os, shutil, signal, subprocess, sys, threading, time

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
try:
    import gsheet  # noqa: E402
except Exception:
    gsheet = None


def rss_tree(pid):
    """pid 와 그 자식들의 RSS(MB). (부모, 합계)"""
    def one(p):
        try:
            with open(f"/proc/{p}/status") as f:
                for ln in f:
                    if ln.startswith("VmRSS"):
                        return int(ln.split()[1]) / 1024
        except Exception:
            pass
        return 0.0
    kids = []
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
        kids = [int(x) for x in out]
    except Exception:
        pass
    allk = list(kids)
    for k in kids:
        try:
            allk += [int(x) for x in subprocess.run(["pgrep", "-P", str(k)], capture_output=True, text=True).stdout.split()]
        except Exception:
            pass
    me = one(pid)
    return me, me + sum(one(k) for k in allk)


class Sampler:
    def __init__(self, pid):
        self.pid, self.peak_p, self.peak_t, self.stop = pid, 0.0, 0.0, False
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.stop:
            p, t = rss_tree(self.pid)
            self.peak_p, self.peak_t = max(self.peak_p, p), max(self.peak_t, t)
            time.sleep(0.25)

    def reset(self):
        self.peak_p, self.peak_t = rss_tree(self.pid)


def wait_idle(page, extra=1.5, limit=600):
    """Streamlit 이 다 그릴 때까지 기다린다 (실행 중 표시가 사라질 때까지)."""
    t0 = time.time()
    time.sleep(1.0)
    while time.time() - t0 < limit:
        try:
            run = page.locator('[data-testid="stStatusWidget"]').count()
        except Exception:
            run = 0
        if not run:
            time.sleep(extra)
            if not page.locator('[data-testid="stStatusWidget"]').count():
                return time.time() - t0
        time.sleep(0.5)
    return time.time() - t0


def main():
    code_dir, name, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    if not os.environ.get("KEEP_DATA"):               # (KEEP_DATA=1 은 로컬 시험용: 있는 자료 그대로)
        info = gsheet._info()
        os.makedirs(os.path.join(code_dir, ".streamlit"), exist_ok=True)
        with open(os.path.join(code_dir, ".streamlit", "secrets.toml"), "w") as f:
            f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
        for d in ("scout_data", "saved_data"):           # 재부팅한 것처럼 자료 없이 시작
            shutil.rmtree(os.path.join(code_dir, d), ignore_errors=True)
    errf = open(f"/tmp/sitemem_{name}.err", "w+")
    env = dict(os.environ)
    env.pop("GCP_SERVICE_ACCOUNT", None)
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "scouting.py", "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, env=env, stdout=subprocess.DEVNULL, stderr=errf, text=True,
                           start_new_session=True)
    time.sleep(6)
    sm = Sampler(srv.pid)
    rows = []

    def rec(label, secs, ok=""):
        p, t = rss_tree(srv.pid)
        rows.append((label, p, t, sm.peak_p, sm.peak_t, secs, ok))
        print(f"[{name}] {label:<34} 지금 {p:6.0f} MB (자식포함 {t:6.0f}) · 이 단계 최고 {sm.peak_p:6.0f} MB "
              f"(자식포함 {sm.peak_t:6.0f}) · {secs:5.0f}초 {ok}", flush=True)
        sm.reset()

    from playwright.sync_api import sync_playwright
    base = f"http://localhost:{port}"
    with sync_playwright() as pw:
        _exe = os.environ.get("LOCAL_CHROME")
        br = pw.chromium.launch(**({"executable_path": _exe, "args": ["--no-sandbox"]} if _exe else {}))
        pg = br.new_page(viewport={"width": 1400, "height": 1000})
        pg.set_default_timeout(20000)

        def go(q):
            t0 = time.time()
            pg.goto(base + "/" + q, wait_until="domcontentloaded", timeout=120000)
            wait_idle(pg)
            # 자료를 처음 만드는 중이면 끝날 때까지 기다린다 (화면이 스스로 바뀜). 실패 안내면 '그냥 화면 열기'.
            while time.time() - t0 < 900:
                if pg.get_by_text("처음 준비하고 있습니다").count() == 0:
                    break
                b = pg.get_by_role("button", name="그냥 화면 열기")
                if b.count() and pg.get_by_text("받아오지 못했습니다").count():
                    b.first.click(); wait_idle(pg); break
                time.sleep(2)
            wait_idle(pg)
            return time.time() - t0

        def pick(label_re, kw, opt_kw):
            """검색어 넣고 선택 상자에서 고르기. 성공하면 '선택됨'."""
            import re as _re
            try:
                pg.get_by_label(_re.compile(label_re)).first.fill(kw)
                pg.keyboard.press("Enter"); wait_idle(pg, 1.0)
                cb = pg.get_by_role("combobox", name=_re.compile(r"선택")).first
                cb.click(); time.sleep(0.8)
                op = pg.get_by_role("option", name=_re.compile(opt_kw)).first
                op.click()
                wait_idle(pg)
                return "선택됨"
            except Exception as e:
                return "못 고름(" + type(e).__name__ + ")"

        rec("0. 서버 켬", 0)
        s = go(""); rec("1. 홈", s)
        s = go("?page=search"); rec("2. 종합분석 열기 (허가·약가 준비)", s)
        t = time.time(); ok = pick(r"검색어", "릭시아나", "릭시아나"); rec("3. 종합분석 '릭시아나' (IQVIA)", time.time() - t, ok)
        s = go("?page=price"); rec("4. 약가 분석 열기", s)
        t = time.time(); ok = pick(r"검색어", "에독사반", "에독사반"); rec("5. 약가 '에독사반' 성분", time.time() - t, ok)
        s = go("?page=sales"); rec("6. 매출 분석 화면", s)
        t = time.time()
        try:
            pg.get_by_role("button", name="📊 매출 분석 불러오기").click(); wait_idle(pg, 2.0, limit=900); ok = "불러옴"
        except Exception as e:
            ok = "못 누름(" + type(e).__name__ + ")"
        rec("7. 매출 분석 불러오기 (IQVIA·UBIST)", time.time() - t, ok)
        t = time.time()
        try:
            pg.get_by_role("tab", name=__import__("re").compile("시장")).first.click(); wait_idle(pg); ok = "시장 분석 탭"
        except Exception as e:
            ok = "탭 못 누름(" + type(e).__name__ + ")"
        rec("8. 매출 시장 분석 탭", time.time() - t, ok)
        s = go("?page=search"); rec("9. 다시 종합분석", s)
        t = time.time(); ok = pick(r"검색어", "리피토", "리피토"); rec("10. 종합분석 '리피토'", time.time() - t, ok)
        s = go("?page=sales"); t = time.time()
        try:
            pg.get_by_role("button", name="📊 매출 분석 불러오기").click(); wait_idle(pg, 2.0, limit=900); ok = "불러옴"
        except Exception as e:
            ok = "못 누름(" + type(e).__name__ + ")"
        rec("11. 매출 분석 한 번 더", s + time.time() - t, ok)
        time.sleep(20); rec("12. 20초 쉬고 나서", 20)
        br.close()
    sm.stop = True
    peak = max(r[3] for r in rows)
    peak_t = max(r[4] for r in rows)
    last = rows[-1][1]
    print(f"\n[{name}] 서버 최고 {peak:.0f} MB · 자식포함 최고 {peak_t:.0f} MB · 끝났을 때 {last:.0f} MB")
    os.killpg(srv.pid, signal.SIGTERM)
    try:
        srv.wait(timeout=20)
    except Exception:
        pass
    errf.seek(0); err = errf.read()
    bad = [ln for ln in err.splitlines() if ("Traceback" in ln or "Error" in ln) and "Warning" not in ln]
    print(f"[{name}] 서버 오류 줄 {len(bad)}개" + ("" if not bad else ": " + " | ".join(b[:120] for b in bad[:5])))
    with open(f"/tmp/sitemem_{name}.json", "w") as f:
        json.dump(rows, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
