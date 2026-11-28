"""제품 제안 '원료사 동향(DMF)'·'약물 재창출': v2 vs 지금 사이트 — 같은 조작, 결과가 같은지 + 서버 메모리.
사이트에 쓰지 않는다. 매출 숫자는 적지 않는다(매출이 섞인 칸은 두 사이트 지문이 같은지만 본다).
사용: python check_v2_dmfrp.py <코드폴더> <이름> <포트> <시작파일> <제품제안주소> <결과json>"""
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
    code_dir, name, port, entry, p_sugg, out = sys.argv[1:7]
    port = int(port)
    errf = open(f"/tmp/v2dr_{name}.err", "w+")
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", entry, "--server.headless", "true",
                            "--server.port", str(port), "--browser.gatherUsageStats", "false"],
                           cwd=code_dir, stdout=subprocess.DEVNULL, stderr=errf, start_new_session=True)
    time.sleep(7)
    peak, stop = [0.0], [False]

    def samp():
        while not stop[0]:
            peak[0] = max(peak[0], rss(srv.pid)); time.sleep(0.2)
    threading.Thread(target=samp, daemon=True).start()
    R = {}

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

    def exc(pg):
        return pg.locator('[data-testid="stException"]').count()

    def dmf_read(pg, lab, t):
        body = pg.inner_text("body")
        cap = re.findall(r"원료사 새 등록 [\d,]+건 \(그 전 \d+개월 [\d,]+건\)|조건 맞는 성분 [\d,]+개|오리지널사 본인 등록 \d+건 뺌", body)
        pick = re.findall(r"성분 자세히 보기\s*\n+\s*성분\s*\n+\s*([^\n]+)", body)
        mets = re.findall(r"(원료사 \(\d+개월\)|새 제조소|성분 확인 생동·1상|코드명 의심|물질특허 만료)\s*\n+\s*([^\n]+)", body)
        sales = re.findall(r"매출\(억[^\n]*\)\s*\n+\s*([^\n]+)", body)
        R[lab] = {"공개": cap, "첫 성분": pick[:1], "지표": mets[:6],
                  "매출지문": hashlib.md5("|".join(sales).encode()).hexdigest()[:10], "오류": exc(pg)}
        rec(lab, t, f"{' · '.join(cap)} · 첫 성분 {pick[:1]} · 오류 {exc(pg)}")

    with sync_playwright() as pw:
        br = pw.chromium.launch(); pg = br.new_page(viewport={"width": 1500, "height": 1100})
        pg.set_default_timeout(30000)
        rec("0. 서버 켬", time.time())
        t = time.time()
        pg.goto(f"http://localhost:{port}/{p_sugg}", timeout=180000); idle(pg, 2)
        while pg.get_by_text("처음 준비하고 있습니다").count() and time.time() - t < 900:
            time.sleep(3)
        idle(pg)
        rec("1. 제품 제안 (조건 필터)", t, f"오류 {exc(pg)}")
        try:
            t = time.time(); pg.get_by_text("③ 경쟁사 현황 (DMF·생동)", exact=True).first.click(); idle(pg, 2)
            dmf_read(pg, "2. DMF 기본(원료사 12개월)", t)
            t = time.time(); pg.get_by_text("추가 신청 증가", exact=True).first.click(); idle(pg, 2)
            dmf_read(pg, "3. DMF 추가 신청 증가", t)
            t = time.time(); pg.get_by_text("원료사 새 등록 증가", exact=True).first.click(); idle(pg)
            pg.get_by_label(re.compile("기간\\(개월\\)")).fill("24"); pg.keyboard.press("Enter"); idle(pg, 2)
            dmf_read(pg, "4. DMF 24개월", t)
            t = time.time(); pg.get_by_text("UBIST", exact=True).first.click(); idle(pg, 2)
            dmf_read(pg, "5. DMF 24개월 UBIST", t)
        except Exception as e:
            rec("DMF", time.time(), "못 함 " + type(e).__name__ + " " + str(e)[:80])
        for lab, mode, q in [("6. 재창출 약 '실데나필'", "💊 약으로", "실데나필"),
                             ("7. 재창출 질환 'Raynaud disease'", "🩺 질환으로", "Raynaud disease")]:
            t = time.time()
            try:
                pg.get_by_text("④ 약물 재창출", exact=True).first.click(); idle(pg)
                pg.get_by_text(mode, exact=True).first.click(); idle(pg)
                pg.get_by_label(re.compile("약 이름|질환 이름")).first.fill(q); pg.keyboard.press("Enter"); idle(pg)
                pg.get_by_role("button", name=re.compile("지도 그리기")).first.click(); idle(pg, 3, 300)
                body = pg.inner_text("body")
                info = re.findall(r"허가 품목 \d+개[^\n]*|표적 \d+개[^\n]{0,40}|질환 \d+개[^\n]{0,40}", body)[:3]
                R[lab] = {"요약": info, "그림": pg.locator(".js-plotly-plot").count(), "오류": exc(pg)}
                rec(lab, t, f"그림 {pg.locator('.js-plotly-plot').count()} · {info[:2]} · 오류 {exc(pg)}")
            except Exception as e:
                R[lab] = {"실패": type(e).__name__}
                rec(lab, t, "못 함 " + type(e).__name__ + " " + str(e)[:80])
        br.close()
    time.sleep(8); rec("끝 (8초 쉬고)", time.time())
    stop[0] = True
    os.killpg(srv.pid, signal.SIGTERM)
    errf.seek(0); bad = [l for l in errf.read().splitlines() if "Traceback" in l or "Error" in l]
    R["_mem"] = {"최고": round(peak[0])}
    print(f"[{name}] 최고 {peak[0]:.0f} MB · 서버 오류 줄 {len(bad)}" + ("" if not bad else ": " + " | ".join(x[:200] for x in bad[:6])))
    json.dump(R, open(out, "w"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
