"""be_calc 을 외부 계산기(PowerTOST) 결과와 대조한다.

    python3 tests/check_be_calc.py

be_calc.py 를 고친 뒤에는 이걸 돌려 숫자가 그대로인지 확인한다.
기준값은 tests/be_reference.json — biostatistics.ca 의 PowerTOST 계산기에
직접 넣어 받은 89점이다.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import be_calc  # noqa: E402

ALIAS = {"2x2x2": "2x2"}          # PowerTOST 에서 2x2 와 같은 설계


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ref = json.load(open(os.path.join(here, "be_reference.json"), encoding="utf-8"))
    pts = ref["points"]
    bad = []
    t0 = time.time()
    for r in pts:
        k = ALIAS.get(r["design"], r["design"])
        kw = dict(gmr=r["gmr"], alpha=r["alpha"] / 100, lo=r["lo"], hi=r["hi"])
        n = be_calc.sample_size(k, r["cv"] / 100, target=r["target"] / 100, **kw)
        p = be_calc.power(k, n, r["cv"] / 100, **kw) * 100 if n else None
        if n != r["n"]:
            bad.append(f"{k} CV{r['cv']} GMR{r['gmr']} 검정력{r['target']} "
                       f"α{r['alpha']} {r['lo']}-{r['hi']}: 인원 {n} ≠ {r['n']}")
        elif p is None or abs(p - r["power"]) > 0.011:
            bad.append(f"{k} CV{r['cv']}: 검정력 {p:.2f}% ≠ {r['power']:.2f}%")
    dt = time.time() - t0
    print(f"{len(pts)}점 대조 · {dt:.1f}초")
    if bad:
        print(f"\n불일치 {len(bad)}건:")
        for b in bad:
            print("  -", b)
        return 1
    print("전부 일치 (인원·달성검정력)")

    n = be_calc.sample_size("2x2", 0.30)
    p = be_calc.power("2x2", n, 0.30)
    ok = (n == 40 and abs(p - 0.815845) < 5e-7)
    print(f"PowerTOST 문서 예제  2x2 · CV30% → {n}명 · {p:.6f} "
          f"(기대 40 · 0.815845)  {'OK' if ok else 'X'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
