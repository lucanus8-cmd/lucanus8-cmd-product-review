"""사이트에 넣을 ctgov.py · meta_calc.py 를 실제 ClinicalTrials.gov · PubMed 로 돌려 본다.

화면과 같은 함수(ctgov.search → meta_calc)를 쓰므로, 여기 숫자가 사이트에 나올 숫자다.
공개 자료만 적는다. 읽기만 한다.
"""
import math
import os
import sys
import time

sys.path.insert(0, os.getcwd())
import ctgov      # noqa: E402
import meta_calc as mc  # noqa: E402

CASES = [
    ("bempedoic acid", "ezetimibe", "연속형", "LDL", 12, ("3상", "4상"), "MD", ""),
    ("empagliflozin 10", "dapagliflozin 10", "연속형", "HbA1c", 24, ("3상",), "MD", "type 2 diabetes"),
    ("empagliflozin", "dapagliflozin", "생존분석", "cardiovascular death, heart failure", 0, ("3상",), "HR", ""),
    ("empagliflozin", "dapagliflozin", "이분형", "hypoglycemia, hypoglycaemia", 0, ("3상",), "RR", ""),
]
if os.environ.get("CASE"):
    a, b, k, o, w, ph, m, cd, cp = (os.environ["CASE"].split(";") + ["", ""])[:9]
    CASES = [(a, b, k, o, int(w or 0), tuple(ph.split(",")), m, cd, cp)]


def es(kind, measure, r):
    if kind == "연속형":
        return mc.es_cont(measure, r.get("n1"), r.get("m1"), r.get("sd1"), r.get("n2"), r.get("m2"), r.get("sd2"),
                          r.get("d"), r.get("lo"), r.get("hi"))
    if kind == "이분형":
        return mc.es_bin(measure, r.get("e1"), r.get("n1"), r.get("e2"), r.get("n2"))
    return mc.es_surv(r.get("hr"), r.get("lo"), r.get("hi"))


def run(a, b, kind, out, week, phases, measure, cond, comp=""):
    print(f"\n######## {a} vs {b} · {kind} · '{out}' · {week or '시점 무관'}주 · {'/'.join(phases)} · {measure} · 질환 '{cond}'")
    res = ctgov.search(a, b, kind, out, week, phases, cond=cond, comp=comp)
    print(f"공통 비교약: {comp or '위약'}")
    print(f"받음: A {res['found']['A']}개 · B {res['found']['B']}개 · {res['sec']:.1f}초 · 500개에서 끊김 {res['capped']}")
    for k, v in res["skipped"].items():
        print(f"   빠짐 {k}: {v}")
    try:
        t = time.time()
        pubs = ctgov.pubmed_for_ncts([r["임상번호"] for r in res["rows"]])
        print(f"PubMed 논문 연결 {len(pubs)}개 ({time.time() - t:.1f}초)")
    except Exception as e:
        pubs = {}
        print("PubMed 실패:", str(e)[:150])
    pooled = {}
    for side in ("A vs 위약", "B vs 위약", "A vs B (직접)"):
        rs = [r for r in res["rows"] if r["비교"] == side]
        print(f"\n== {side}: {len(rs)}줄")
        y, v = [], []
        for r in rs:
            yy, vv, how = es(kind, measure, r)
            p = pubs.get(r["임상번호"])
            ptxt = f"{p[0]} {p[1]}" if p else "-"
            val = mc.fmt_ci(measure, yy, math.sqrt(vv)) if yy is not None else f"(계산 안 됨: {how})"
            print(f"  {'☑' if r['합침'] else '☐'} {r['임상번호']} {val} · {r['시험군'][:30]} vs {r['대조군'][:25]}"
                  f" · 점검: {r['점검']} · 논문: {ptxt}\n      {r['제목'][:95]}")
            if r["합침"] and yy is not None:
                y.append(yy)
                v.append(vv)
        if y:
            p = mc.pool(y, v)
            pooled[side] = p
            print(f"  ▶ 합친 값(랜덤): {mc.fmt_ci(measure, p['re'], p['re_se'])} · 임상 {p['k']} · I² {p['i2']:.0f}%")
    if "A vs 위약" in pooled and "B vs 위약" in pooled:
        pa, pb = pooled["A vs 위약"], pooled["B vs 위약"]
        d, se, pv = mc.bucher(pa["re"], pa["re_se"], pb["re"], pb["re_se"])
        print(f"\n▶ 간접 비교 A−B: {mc.fmt_ci(measure, d, se)} · p={pv:.2f}")
        print("  " + mc.verdict(measure, d, se, True, a, b, out))


def main():
    for c in CASES:
        try:
            run(*c)
            time.sleep(1.5)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("실패:", e)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
