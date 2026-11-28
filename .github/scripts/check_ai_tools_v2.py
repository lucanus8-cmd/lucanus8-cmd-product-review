"""AI 검색 도구: 예전 판(ai_tools, 표 전체를 올림) vs v2 가벼운 판(ai_tools_v2, DuckDB) — 결과가 같은지 + 메모리.
사이트에 쓰지 않는다. 매출이 든 결과는 내용을 적지 않고 '같음/다름'만.
사용: python check_ai_tools_v2.py <old|v2> <결과json>   ·   python check_ai_tools_v2.py cmp <old.json> <v2.json>"""
import json, os, sys, threading, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
from check_ai_tools import PLANS, SECRET_TOOLS  # noqa: E402

PLANS = PLANS + [("임상 (v2 새 도구)", [("clinical_summary", {"ingredient": "에독사반"}),
                                    ("clinical_summary", {"ingredient": "미라베그론"})])]


def rss():
    with open("/proc/self/status") as f:
        for ln in f:
            if ln.startswith("VmRSS"):
                return int(ln.split()[1]) / 1024
    return 0.0


def run(mode, out):
    import streamlit  # noqa
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    if mode == "old":
        import ai_tools as at
    else:
        sys.path.insert(0, os.path.join(ROOT, "v2"))
        from core import ai_tools_v2 as at
    peak = [rss()]; stop = [False]

    def samp():
        while not stop[0]:
            peak[0] = max(peak[0], rss()); time.sleep(0.1)
    threading.Thread(target=samp, daemon=True).start()
    base = rss()
    res = {}
    for rnd in (1, 2):
        t = time.time()
        for q, steps in PLANS:
            for i, (name, args) in enumerate(steps):
                if mode == "old" and name not in at.FUNCS:
                    continue
                t0 = time.time(); r = at.call(name, args); dt = time.time() - t0
                key = f"{q} #{i} {name}"
                res[key] = r
                if rnd == 1:
                    print(f"[{mode}] {name:<17} {dt*1000:6.0f}ms · 메모리 {rss():5.0f} MB"
                          + ("" if "오류" not in r else f" · 오류 {r['오류']}"), flush=True)
        print(f"[{mode}] {rnd}번째 전체 {time.time()-t:.1f}초 · 최고 {peak[0]:.0f} MB · 지금 {rss():.0f} MB", flush=True)
    stop[0] = True
    print(f"[{mode}] ★ 시작 {base:.0f} MB → 최고 {peak[0]:.0f} MB (도구 때문에 늘어난 양 {peak[0]-base:.0f} MB) · 끝 {rss():.0f} MB")
    json.dump(res, open(out, "w"), ensure_ascii=False, default=str)


def cmp(a, b):
    A, B = json.load(open(a)), json.load(open(b))
    same = diff = 0
    for k in B:
        if k not in A:
            continue
        name = k.rsplit(" ", 1)[-1]
        x, y = A[k].get("결과"), B[k].get("결과")
        if name == "sales_summary" and isinstance(y, dict):
            y = {kk: vv for kk, vv in y.items() if kk != "연도별 합계(억)"}
        ok = json.dumps(x, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(y, ensure_ascii=False, sort_keys=True, default=str)
        same += ok; diff += (not ok)
        line = f"{'같음' if ok else '다름'} · {k}"
        if not ok and name not in SECRET_TOOLS:
            line += f"\n      예전: {json.dumps(x, ensure_ascii=False, default=str)[:260]}\n      v2  : {json.dumps(y, ensure_ascii=False, default=str)[:260]}"
        print(line)
    print(f"── 같음 {same} · 다름 {diff}")
    for k in B:
        if k not in A:
            print("v2 새 도구:", k, json.dumps(B[k], ensure_ascii=False, default=str)[:300])


if __name__ == "__main__":
    if sys.argv[1] == "cmp":
        cmp(sys.argv[2], sys.argv[3])
    else:
        run(sys.argv[1], sys.argv[2])
