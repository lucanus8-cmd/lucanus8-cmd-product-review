"""사이트 메타분석 계산(meta_calc.py)을 R metafor 기준값·논문 발표값과 나란히 비교한다. 사이트와 상관없다.

metaref.R 이 남긴 ref/*.csv 를 읽는다. 공개 논문 자료만 다룬다.
"""
import csv
import math
import os
import sys

sys.path.insert(0, os.getcwd())
import meta_calc as mc  # noqa: E402


def rows(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def f(x):
    try:
        return float(x)
    except Exception:
        return None


REF = {r["name"]: {k: f(v) for k, v in r.items() if k != "name"} for r in rows("ref/results.csv")}
PAPER = {"BCG RR random(DL)": "Colditz 1994 JAMA: RR 0.49 (0.34–0.70)",
         "Hackshaw OR random(DL)": "Hackshaw 1997 BMJ: 1.24 (1.13–1.36)"}
bad = []


def compare(name, ys, vs, back=lambda x: x, which="re"):
    p = mc.pool(ys, vs)
    est, se = (p["re"], p["re_se"]) if which == "re" else (p["fe"], p["fe_se"])
    mine = {"est": back(est), "lo": back(est - mc.Z * se), "hi": back(est + mc.Z * se),
            "tau2": p["tau2"] if which == "re" else 0.0, "I2": p["i2"], "Q": p["q"], "k": p["k"]}
    ref = REF[name]
    print(f"\n== {name}  (임상 {p['k']}개)")
    for key in ("est", "lo", "hi", "tau2", "I2", "Q"):
        a, b = mine[key], ref.get(key)
        if b is None or (key == "tau2" and which != "re"):
            continue
        ok = abs(a - b) <= 1e-4 * max(1.0, abs(b))
        print(f"   {key:5s} 사이트 {a:12.6f} · metafor {b:12.6f}  {'같음' if ok else '다름!'}")
        if not ok:
            bad.append(f"{name} {key}")
    if name in PAPER:
        print(f"   논문 발표값 → {PAPER[name]} · 사이트 {mine['est']:.2f} ({mine['lo']:.2f}–{mine['hi']:.2f})")


bcg = rows("ref/bcg.csv")
for meas, back in (("RR", math.exp), ("OR", math.exp), ("RD", lambda x: x)):
    ys, vs = [], []
    for r in bcg:
        a, b, c, d = (int(float(r[k])) for k in ("tpos", "tneg", "cpos", "cneg"))
        y, v, _ = mc.es_bin(meas, a, a + b, c, c + d)
        ys.append(y)
        vs.append(v)
    compare(f"BCG {meas} random(DL)", ys, vs, back)
    if meas == "RR":
        compare("BCG RR fixed", ys, vs, back, "fe")

hk = rows("ref/hackshaw.csv")
ys, vs = [], []
for r in hk:     # 논문 표처럼 연구별 OR 과 95% CI 만 넣는다 (사이트의 '비 + 신뢰구간' 입력과 같다)
    y, v, _ = mc.es_surv(f(r["or"]), f(r["or.lb"]), f(r["or.ub"]))
    ys.append(y)
    vs.append(v)
compare("Hackshaw OR random(DL)", ys, vs, math.exp)
compare("Hackshaw OR fixed", ys, vs, math.exp, "fe")

nm = rows("ref/normand.csv")
for meas in ("MD", "SMD"):
    ys, vs = [], []
    for r in nm:
        y, v, _ = mc.es_cont(meas, f(r["n1i"]), f(r["m1i"]), f(r["sd1i"]), f(r["n2i"]), f(r["m2i"]), f(r["sd2i"]),
                             None, None, None)
        ys.append(y)
        vs.append(v)
    compare(f"Normand {meas} random(DL)", ys, vs)
    if meas == "MD":
        compare("Normand MD fixed", ys, vs, which="fe")

print("\n" + (f"다른 곳 {len(bad)}개: {bad}" if bad else "모두 같음 (소수 넷째 자리까지)"))
sys.exit(1 if bad else 0)
