"""meta_calc 을 공개 예제·손계산과 대조한다.

    python3 tests/check_meta_calc.py

1) BCG 백신 13개 임상 (metafor dat.bcg) — 위험비 RR, DerSimonian-Laird.
   metafor 공개값: RR 0.4896 [0.3449, 0.6950], τ² 0.3088, Q 152.23, I² 92.12%, 고정효과 RR 0.6500
2) SMD(Hedges g)·0건 보정·HR·카이제곱 p 값 손계산
3) 벰페도익산 위약 대조 5개 임상(ClinicalTrials.gov 실제 값) — 로봇 결과와 같은지
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import meta_calc as mc  # noqa: E402

BCG = [(4, 119, 11, 128), (6, 300, 29, 274), (3, 228, 11, 209), (62, 13536, 248, 12619),
       (33, 5036, 47, 5761), (180, 1361, 372, 1079), (8, 2537, 10, 619), (505, 87886, 499, 87892),
       (29, 7470, 45, 7232), (17, 1699, 65, 1600), (186, 50448, 141, 27197), (5, 2493, 3, 2338),
       (27, 16886, 29, 17825)]

bad = []


def eq(name, got, want, tol):
    ok = got is not None and abs(got - want) <= tol
    print(f"  {'OK' if ok else 'X '} {name}: {got if got is None else round(got, 6)} (기대 {want})")
    if not ok:
        bad.append(name)


def main():
    print("1) BCG 13개 임상 · RR · DerSimonian-Laird")
    y, v = [], []
    for tpos, tneg, cpos, cneg in BCG:
        yy, vv, _ = mc.es_bin("RR", tpos, tpos + tneg, cpos, cpos + cneg)
        y.append(yy)
        v.append(vv)
    p = mc.pool(y, v)
    m, lo, hi = mc.ci("RR", p["re"], p["re_se"])
    eq("RR", m, 0.4896, 5e-4)
    eq("하한", lo, 0.3449, 5e-4)
    eq("상한", hi, 0.6950, 5e-4)
    eq("τ²", p["tau2"], 0.3088, 5e-4)
    eq("Q", p["q"], 152.2330, 5e-3)
    eq("I²", p["i2"], 92.12, 5e-3)
    eq("고정효과 RR", math.exp(p["fe"]), 0.6500, 5e-4)
    eq("Q 의 p (매우 작음)", p["p_q"], 0.0, 1e-20)
    eq("가중치 합", sum(p["w_re"]), 100.0, 1e-9)

    print("2) 손계산")
    g, vg, _ = mc.es_cont("SMD", 20, 1, 1, 20, 0, 1)
    eq("Hedges g", g, 1 - 3 / 151, 1e-12)
    eq("g 분산", vg, 0.1 + (1 - 3 / 151) ** 2 / 80, 1e-12)
    d, vd, how = mc.es_cont("MD", d=-0.5, lo=-0.9, hi=-0.1)
    eq("MD (차이·신뢰구간)", d, -0.5, 1e-12)
    eq("MD 표준오차", math.sqrt(vd), 0.4 / mc.Z, 1e-12)
    d2, vd2, _ = mc.es_cont("MD", 100, 2.0, 1.0, 100, 1.0, 2.0)
    eq("MD (군별)", d2, 1.0, 1e-12)
    eq("MD 분산 (군별)", vd2, 1 / 100 + 4 / 100, 1e-12)
    r0, v0, memo = mc.es_bin("RR", 0, 10, 5, 10)
    eq("0건 보정 RR", r0, math.log((0.5 / 11) / (5.5 / 11)), 1e-12)
    print("    메모:", memo)
    nn = mc.es_bin("OR", 0, 10, 0, 10)
    print(f"  {'OK' if nn[0] is None else 'X '} 두 군 0건 → 계산 안 함 ({nn[2]})")
    if nn[0] is not None:
        bad.append("두 군 0건")
    h, vh, _ = mc.es_surv(0.8, 0.7, 0.92)
    eq("log HR", h, math.log(0.8), 1e-12)
    eq("HR 표준오차", math.sqrt(vh), (math.log(0.92) - math.log(0.7)) / (2 * mc.Z), 1e-12)
    eq("카이제곱 p (3.8415, 1)", mc._chi2_sf(3.841458820694124, 1), 0.05, 1e-9)
    eq("카이제곱 p (10, 10)", mc._chi2_sf(10, 10), 0.4404932851, 1e-8)
    eq("카이제곱 p (2, 4)", mc._chi2_sf(2, 4), 0.7357588823, 1e-8)
    dd, ss, pp = mc.bucher(-21.42, 1.80, -20.98, 2.42)
    eq("Bucher 차이", dd, -0.44, 1e-12)
    eq("Bucher 표준오차", ss, math.sqrt(1.80 ** 2 + 2.42 ** 2), 1e-12)

    print("3) 벰페도익산 5개 임상 (로봇 결과와 같은지)")
    rows = [(-21.41, -25.13, -17.69), (-17.42, -20.95, -13.89), (-28.45, -34.37, -22.53),
            (-19.00, -27.44, -10.56), (-21.78, -26.71, -16.85)]
    y, v = [], []
    for d, lo, hi in rows:
        a, b, _ = mc.es_cont("MD", d=d, lo=lo, hi=hi)
        y.append(a)
        v.append(b)
    p = mc.pool(y, v)
    eq("합친 차이", p["re"], -21.42, 5e-3)
    eq("I²", p["i2"], 61, 0.5)
    loo = mc.leave_one_out(y, v, "랜덤효과")
    print(f"  하나씩 빼 보기: {[round(x[0], 2) for x in loo]}")

    print("4) 결론 문장")
    print("  ", mc.verdict("MD", -0.44, 3.0, True, "A", "B", "LDL-C"))
    print("  ", mc.verdict("MD", -5.0, 1.0, True, "A", "B", "LDL-C"))
    print("  ", mc.verdict("RR", math.log(1.5), 0.1, True, "A", "B", "이상반응"))

    print("\n" + ("전부 맞음" if not bad else f"틀림 {len(bad)}개: {bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
