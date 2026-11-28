"""질병명(효능·효과) 필터 시험 — 작업 가지 코드를 로봇 안에서 띄워 본다. 사이트(Streamlit Cloud)에는 쓰지 않는다.
A. efficacy.py 를 실제 자료로: 색인 크기·시간, 질병명별 걸리는 성분 수와 앞쪽 문구 (공개 자료)
B. 사이트 화면: 제품 제안 → ⑤ 질병명 칸에 넣었을 때 후보 수가 바뀌는지, 오류 없는지, 서버 메모리
매출 숫자는 적지 않는다(후보 '개수'만)."""
import base64, json, os, re, signal, subprocess, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402

TERMS = [("당뇨", ""), ("고혈압", ""), ("고혈압", "폐동맥"), ("고지혈증, 이상지질혈증", ""), ("골다공증", ""),
         ("과민성 방광", ""), ("뇌전증, 간질", "간질환, 간질성"), ("위식도역류", ""), ("천식", ""), ("알츠하이머, 치매", ""),
         ("편두통", ""), ("탈모", ""), ("B형 간염", ""), ("전립선비대, 전립샘비대", "")]


def rss(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for ln in f:
                if ln.startswith("VmRSS"):
                    return int(ln.split()[1]) / 1024
    except Exception:
        return 0.0


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd
    import bootstrap, scout_sources as ss
    print("자료 만들기:", bootstrap._do_build(include_sales=True, force=False))
    ss._ensure = lambda *a, **k: None

    # ── A ──
    import efficacy as ee
    print("효능 자료 기준:", ee.meta())
    import resource
    r0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    t = time.time(); idx, cmp_ = ee.index(); t1 = time.time() - t
    print(f"성분 색인 {len(idx):,}개 · 만드는 데 {t1:.1f}초 · 메모리 {(idx.memory_usage(deep=True)+cmp_.memory_usage(deep=True))/1e6:.1f} MB")
    ap = ss.load_approval()
    core = ap.drop_duplicates("_key").set_index("_key")["주성분"].astype(str).map(
        lambda s: re.sub(r"\[[^\]]*\]|\([^)]*\)", "", s)[:24])
    keys = pd.Series(idx.index, index=idx.index)
    for w, ex in TERMS:
        t = time.time(); r = ee.search(keys, w, ex); dt = (time.time() - t) * 1000
        h = r[r["걸림"]].copy()
        h["_b"] = (h["위치"] != "첫머리").astype(int)
        h = h.sort_values(["_b", "_ee_rank"])
        print(f"\n[{w}{' · 빼기: ' + ex if ex else ''}] {len(h)}개 성분 (첫머리 {int((h['위치']=='첫머리').sum())}) · {dt:.0f}ms")
        for k, row in list(h.iterrows())[:5]:
            print(f"   {row['위치']:<3} {core.get(k, k)}: {row['문구'][:110]}")
        tail = list(h.iterrows())[-2:] if len(h) > 7 else []
        for k, row in tail:
            print(f"   … {row['위치']:<3} {core.get(k, k)}: {row['문구'][:110]}")

    # ── B ── 사이트 화면
    print("\n── 사이트 화면 시험 (제품 제안) ──")
    errf = open("/tmp/ee_site.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "scouting.py", "--server.headless", "true",
                            "--server.port", "8631", "--browser.gatherUsageStats", "false"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(8)
    from playwright.sync_api import sync_playwright

    def idle(pg, extra=1.5, limit=600):
        t0 = time.time(); time.sleep(1)
        while time.time() - t0 < limit:
            if not pg.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(0.5)

    def count(pg):
        m = re.search(r"조건 충족 후보 ([\d,]+)개", pg.inner_text("body"))
        return m.group(1) if m else "?"

    def errors(pg):
        return pg.locator('[data-testid="stException"]').count()

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1400, "height": 1000})
        pg.set_default_timeout(30000)
        pg.goto("http://localhost:8631/?page=sugg", timeout=120000); idle(pg, 2, 900)
        b = pg.get_by_role("button", name=re.compile("매출|불러오기"))
        if "준비되지 않았습니다" in pg.inner_text("body") and b.count():
            b.first.click(); idle(pg, 2, 900)
        print(f"처음: 후보 {count(pg)}개 · 오류 {errors(pg)} · 서버 {rss(srv.pid):.0f} MB")
        print("⑤ 칸 보임:", pg.get_by_text(re.compile("질병명\\(효능·효과\\)으로 좁히기")).count() > 0,
              "· ④ ATC 칸 보임:", pg.get_by_text(re.compile("ATC 계열로 좁히기")).count() > 0)
        pg.get_by_text(re.compile("질병명\\(효능·효과\\)으로 좁히기")).first.click(); time.sleep(1.5)
        for w, ex in [("당뇨", ""), ("고혈압", ""), ("고혈압", "폐동맥"), ("고지혈증, 이상지질혈증", "")]:
            pg.get_by_label(re.compile("^질병명")).first.fill(w); pg.keyboard.press("Enter"); idle(pg)
            pg.get_by_label(re.compile("^빼는 단어")).first.fill(ex); pg.keyboard.press("Enter"); idle(pg)
            col = pg.get_by_text("효능·효과 문구 표시").count() > 0
            print(f"[{w}{' · 빼기: ' + ex if ex else ''}] 후보 {count(pg)}개 · 문구 칸 켜기 보임 {col} · 오류 {errors(pg)} · 서버 {rss(srv.pid):.0f} MB")
        pg.get_by_label(re.compile("^질병명")).first.fill(""); pg.keyboard.press("Enter"); idle(pg)
        print(f"질병명 지운 뒤: 후보 {count(pg)}개 · 오류 {errors(pg)} · 서버 {rss(srv.pid):.0f} MB")
        br.close()
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0)
    bad = [ln for ln in errf.read().splitlines() if "Traceback" in ln or "Error" in ln]
    print(f"서버 오류 줄 {len(bad)}개" + ("" if not bad else ": " + " | ".join(b[:140] for b in bad[:6])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
