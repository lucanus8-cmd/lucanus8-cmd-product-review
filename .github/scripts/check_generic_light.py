"""제네릭 등재: 예전(약가 이력 전체를 올려 계산) vs v2 가벼운 판(필요한 제품 이력만 꺼내 계산) — 결과가 같은지 + 메모리.
사이트에 쓰지 않는다. 공개 자료(약가·허가)만.
사용: python check_generic_light.py old|v2 <결과json>  ·  python check_generic_light.py cmp a.json b.json"""
import json, os, sys, threading, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path[:0] = [os.path.join(ROOT, "v2"), ROOT, os.path.join(ROOT, ".github", "scripts")]


def rss():
    with open("/proc/self/status") as f:
        for ln in f:
            if ln.startswith("VmRSS"):
                return int(ln.split()[1]) / 1024


def pack(r):
    def df(d):
        if d is None or len(d) == 0:
            return []
        return json.loads(d.astype(str).to_json(orient="records", force_ascii=False))
    return {"families": [[str(x) for x in f] for f in r["families"]], "auto": [str(x) for x in (r["auto"] or [])],
            "orig_codes": list(map(str, r["orig_codes"])), "codes": list(map(str, r["codes"])),
            "same": df(r["same"]), "salt": df(r["salt"]), "warn": [{k: str(v) for k, v in w.items()} for w in r["warn"]],
            "n_combo": r["n_combo"], "note": r["note"]}


def run(mode, out):
    import pandas as pd  # noqa: F401
    import streamlit  # noqa: F401
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    import generic_entry as ge
    from check_generic_simple import DEFAULT
    terms = [t.replace("+", " + ") for t in DEFAULT.split(";")]
    pk = [rss()]; stop = [False]

    def samp():
        while not stop[0]:
            pk[0] = max(pk[0], rss()); time.sleep(0.1)
    threading.Thread(target=samp, daemon=True).start()
    base, t0 = rss(), time.time()
    res = {}
    if mode == "old":
        p = ge.prep_price(ss.load_price()); bulk = ge.bulk_dates(p)
        a = ge.prep_approval(ss.load_approval(), ss.ing_core, ss._ap_date)
        print(f"[old] 준비 {time.time()-t0:.1f}초 · 메모리 {rss():.0f} MB", flush=True)
        for t in terms:
            res[t] = pack(ge.analyze(p, a, bulk, t, ss.ing_core))
    else:
        from core import generic_light as GL
        GL._base(*GL._mt())
        print(f"[v2] 준비 {time.time()-t0:.1f}초 · 메모리 {rss():.0f} MB", flush=True)
        for t in terms:
            res[t] = pack(GL.run(t, None, GL._mt()))
    stop[0] = True
    print(f"[{mode}] ★ {len(terms)}개 성분 · {time.time()-t0:.1f}초 · 시작 {base:.0f} → 최고 {pk[0]:.0f} MB (늘어난 양 {pk[0]-base:.0f}) · 끝 {rss():.0f} MB", flush=True)
    json.dump(res, open(out, "w"), ensure_ascii=False)


def cmp(a, b):
    A, B = json.load(open(a)), json.load(open(b))
    same = diff = 0
    for t in A:
        x, y = A[t], B.get(t)
        if x == y:
            same += 1
        else:
            diff += 1
            ks = [k for k in x if x.get(k) != (y or {}).get(k)]
            print(f"다름 · {t} · 다른 항목 {ks}")
            for k in ks[:2]:
                print(f"    예전 {str(x.get(k))[:200]}\n    v2   {str((y or {}).get(k))[:200]}")
    print(f"══ 같음 {same} · 다름 {diff} (성분 {len(A)}개)")


if __name__ == "__main__":
    if sys.argv[1] == "cmp":
        cmp(sys.argv[2], sys.argv[3])
    else:
        run(sys.argv[1], sys.argv[2])
