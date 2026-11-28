"""v2 제품 종합분석·제품 제안 vs 지금 사이트 — 같은 조작, 결과가 같은지 + 서버 메모리.
사이트(Streamlit Cloud)에는 쓰지 않는다. 매출 숫자는 적지 않는다(매출 지표는 두 사이트가 같은지만 '같음/다름').
사용: python check_v2_pages.py <코드폴더> <이름> <포트> <시작파일> <종합분석주소> <제품제안주소> <결과json>"""
import hashlib, json, os, re, signal, subprocess, sys, threading, time

CASES = [("제품명", "리피토", "^리피토정10"), ("주성분", "에독사반", "^에독사반$"), ("제품명", "트윈스타", "^트윈스타정40/5"),
         ("주성분", "로수바스타틴", "^로수바스타틴$")]
PUBLIC = r"허가 [\d,]+건[^\n]*|특허 [\d,]+건[^\n]*|임상 [\d,]+건[^\n]*|약가 매칭 [\d,]+건[^\n]*|동일성분 DMF [\d,]+건[^\n]{0,30}|재심사 매칭 [\d,]+건[^\n]*|매출 매칭 없음"


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
    code_dir, name, port, entry, p_search, p_sugg, out = sys.argv[1:8]
    port = int(port)
    errf = open(f"/tmp/v2pg_{name}.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", entry, "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(7)
    peak, stop = [0.0], [False]

    def samp():
        while not stop[0]:
            peak[0] = max(peak[0], rss(srv.pid)); time.sleep(0.2)
    threading.Thread(target=samp, daemon=True).start()
    result = {}

    def rec(lab, t0, extra=""):
        print(f"[{name}] {lab:<34} 지금 {rss(srv.pid):5.0f} MB · 최고 {peak[0]:5.0f} MB · {time.time()-t0:5.1f}초 {extra}", flush=True)

    from playwright.sync_api import sync_playwright

    def idle(pg, extra=1.5, limit=600):
        t0 = time.time(); time.sleep(1)
        while time.time() - t0 < limit:
            if not pg.locator('[data-testid="stStatusWidget"]').count():
                time.sleep(extra)
                if not pg.locator('[data-testid="stStatusWidget"]').count():
                    return
            time.sleep(0.4)

    def go(pg, path):
        pg.goto(f"http://localhost:{port}/{path}", timeout=180000); idle(pg, 2, 900)
        t0 = time.time()
        while pg.get_by_text("처음 준비하고 있습니다").count() and time.time() - t0 < 900:
            time.sleep(3)
        idle(pg)

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1400, "height": 1000})
        pg.set_default_timeout(30000)
        rec("0. 서버 켬", time.time())
        for basis, kw, opt in CASES:
            t = time.time()
            go(pg, p_search)
            try:
                if basis == "주성분":
                    pg.get_by_text("주성분", exact=True).first.click(); idle(pg)
                pg.get_by_label(re.compile("검색어 입력")).first.fill(kw); pg.keyboard.press("Enter"); idle(pg, 1)
                pg.get_by_role("combobox", name=re.compile("선택")).first.click(); time.sleep(0.8)
                pg.get_by_role("option", name=re.compile(opt)).first.click(); idle(pg)
                body = pg.inner_text("body")
                metrics = re.findall(r"(?:동일성분 )?최신연도\(\d{4}\) 매출\s*\n[^\n]+|매출 CAGR\s*\n[^\n]+|매출상 제조사\s*\n[^\n]+", body)
                toks = re.findall(PUBLIC, body)
                for tn in ["특허", "임상", "약가", "재심사", "DMF"]:
                    pg.get_by_role("tab", name=re.compile(tn)).first.click(); time.sleep(1.2)
                    toks += re.findall(PUBLIC, pg.inner_text("body"))
                exc = pg.locator('[data-testid="stException"]').count()
                result[f"종합:{kw}"] = {"공개": sorted(set(toks)), "매출지문": hashlib.md5("|".join(metrics).encode()).hexdigest()[:10],
                                      "매출칸수": len(metrics), "오류": exc}
                rec(f"종합분석 '{kw}'", t, f"오류 {exc}")
            except Exception as e:
                result[f"종합:{kw}"] = {"실패": type(e).__name__}
                rec(f"종합분석 '{kw}'", t, "못 함 " + type(e).__name__)
        for lab, steps in [("제안 기본(IQVIA 성분별)", []), ("제안 100억·CAGR5", [("size", "100"), ("cagr", "5")]),
                           ("제안 UBIST", [("radio", "UBIST")]), ("제안 제품별", [("radio", "제품별")])]:
            t = time.time()
            try:
                if not steps or lab.startswith("제안 100"):
                    go(pg, p_sugg)
                for kind, v in steps:
                    if kind == "size":
                        pg.get_by_label(re.compile("최소 시장규모")).fill(v); pg.keyboard.press("Enter"); idle(pg)
                    elif kind == "cagr":
                        pg.get_by_label(re.compile("최소 성장 CAGR")).fill(v); pg.keyboard.press("Enter"); idle(pg)
                    else:
                        pg.get_by_text(v, exact=True).first.click(); idle(pg)
                body = pg.inner_text("body")
                n = re.findall(r"조건 충족 후보 ([\d,]+)개", body)
                exc = pg.locator('[data-testid="stException"]').count()
                result[lab] = {"후보": n[0] if n else "?", "오류": exc}
                rec(lab, t, f"후보 {n[0] if n else '?'}개 · 오류 {exc}")
            except Exception as e:
                result[lab] = {"실패": type(e).__name__}
                rec(lab, t, "못 함 " + type(e).__name__)
        br.close()
    time.sleep(8); rec("끝 (8초 쉬고)", time.time())
    stop[0] = True
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or "Error:" in l]
    result["_mem"] = {"최고": round(peak[0]), "끝": round(rss(srv.pid) or 0)}
    print(f"[{name}] 최고 {peak[0]:.0f} MB · 서버 오류 줄 {len(bad)}" + ("" if not bad else ": " + " | ".join(x[:160] for x in bad[:4])))
    json.dump(result, open(out, "w"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
