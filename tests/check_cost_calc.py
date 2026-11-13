"""cost_calc 의 계산이 식대로 도는지 본다.

    python3 tests/check_cost_calc.py

여기 쓰는 단가 P 는 계산을 확인하려고 지어낸 숫자다(기본 단가와 무관).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cost_calc as C  # noqa: E402

P = {"cro_base": 1000, "cro_per_period": 100, "cro_per_subject": 10,
     "lab_base": 2000, "lab_per_sample": 3,
     "site_24h": 100, "site_72h": 148, "site_240h": 316}


def main():
    bad = []

    def eq(name, got, want):
        if abs(got - want) > 1e-6:
            bad.append(f"{name}: {got} ≠ {want}")

    r = C.estimate(10, 2, 5, 1, 24, P)
    eq("검체 수", r["samples"], 100)
    eq("CRO", r["cro"], 1000 + 200 + 100)
    eq("분석", r["lab"], 2000 + 300)
    eq("병원", r["site"], 100 * 10 * 2)
    eq("합계", r["total"], 1300 + 2300 + 2000)
    eq("VAT 포함", r["vat_total"], 5600 * 1.1)

    eq("성분 2개면 분석이 두 배", C.estimate(10, 2, 5, 2, 24, P)["lab"], 4600)

    # 병원 단가: 기준점은 그대로, 사이는 직선, 바깥은 끝값
    for h, want in ((1, 100), (24, 100), (48, 124), (72, 148), (156, 232),
                    (240, 316), (500, 316)):
        eq(f"병원 단가 {h}시간", C.site_unit(h, P), want)

    # 단가가 없거나 이상해도 죽지 않는다
    eq("단가 없음", C.estimate(10, 2, 5, 1, 24, {})["total"], 0)
    eq("단가 None", C.estimate(10, 2, 5, 1, 24, None)["total"], 0)
    eq("글자 단가", C.estimate(10, 2, 5, 1, 24, {"cro_base": "abc"})["total"], 0)
    eq("음수 단가는 0", C.estimate(1, 1, 1, 1, 24, {"cro_base": -5})["total"], 0)
    eq("입력 None", C.estimate(None, None, None, None, None, P)["total"], 1000)
    if C.ready({}) or C.ready(None) or not C.ready(P):
        bad.append("ready 판정")
    if set(C.DEFAULT_PRICES) != set(C.KEYS) or not C.ready(C.DEFAULT_PRICES):
        bad.append("기본 단가의 키가 맞지 않는다")

    if bad:
        print(f"불일치 {len(bad)}건:")
        for b in bad:
            print("  -", b)
        return 1
    print("cost_calc 전부 정상")
    return 0


if __name__ == "__main__":
    sys.exit(main())
