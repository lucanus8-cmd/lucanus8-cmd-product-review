"""dmf_trend 계산 점검 (사이트 없이 돌린다).

    python tests/check_dmf_trend.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dmf_trend as dt  # noqa: E402

bad = []


def ok(cond, what):
    print(("[정상] " if cond else "[실패] ") + what)
    if not cond:
        bad.append(what)


# ── 성분 코어 ──
s = pd.Series(["데스벤라팍신벤조산염", "데스벤라팍신숙신산염일수화물", "벰페도익산", "히알루론산나트륨", "에독사반토실산염수화물"])
ok(dt.core_of(s).tolist() == ["데스벤라팍신", "데스벤라팍신", "벰페도익산", "히알루론산", "에독사반"], "성분 코어")

# ── DMF ── (식약처 칸 이름을 사이트 표준 이름으로 바꾼 꼴)
D = pd.DataFrame({
    "INGR_KOR_NAME": ["가성분", "가성분", "가성분", "가성분", "나성분", "나성분", "다성분"],
    "ENTP_NAME": ["상사A", "상사B", "상사C(1)", "상사D", "한국오리지널(주)", "상사E", "상사F"],
    "MNFCTR_NAME": ["M1", "M2", "M2", "M3", "Orig Plant", "M9", "M5"],
    "MANUF_COUNTRY_CODE_NM": ["인도"] * 7,
    "DMF_PERMIT_DATE": ["2026-03-01", "2026-06-01", "2026-07-01", "2024-06-01", "2026-05-01", "2026-09-01", "2025-01-01"],
    "DMF_PERMIT_NO": ["1-ND", "2-ND", "2-ND(1)", "3-ND", "9-ND", "9-ND(A)", "5-ND"],
    "대상의약품": ["신물질"] * 7,
    "취소/취하구분": ["정상", "정상", "정상", "취하", "정상", "정상", "정상"],
    "문서번호": [""] * 7, "연계심사문서번호": ["v1", "", "", "", "", "", ""],
})
x = dt.prepare_dmf(D)
ok(bool(x.loc[x["신청인"] == "한국오리지널(주)", "오리지널사"].all()), "다국적사 한국법인의 첫 신물질 DMF는 오리지널사로 본다")
ok(int(x["추가"].sum()) == 2, "등록번호 꼬리 (1),(A)는 추가 신청")
t, n = dt.trend(x, "2026-11-14", 12)
r = t.set_index("성분")
ok(r.loc["가성분", "원료사_최근"] == 2 and r.loc["가성분", "원료사_그전"] == 0, "가성분 원료사 최근 2 · 그 전 0 (2024년 것은 24개월 밖)")
ok(r.loc["가성분", "추가_최근"] == 1, "가성분 추가 신청 1")
ok(r.loc["가성분", "새제조소"] == 2, "가성분 새 제조소 2")
ok("나성분" not in r.index or r.loc["나성분", "원료사_최근"] == 0, "오리지널사 등록은 원료사 수에서 빠진다")
ok(n == 1, "뺀 오리지널사 등록 1건")
ok(r.loc["다성분", "원료사_그전"] == 1, "다성분은 그 전 12개월에 1건")
t2, _ = dt.trend(x, "2026-11-14", 12, drop_orig=False)
ok(t2.set_index("성분").loc["나성분", "원료사_최근"] == 1, "빼기를 끄면 오리지널사 등록도 센다")
ap = pd.DataFrame({"신약구분": ["신약"], "주성분": ["가성분염산염"], "업체명": ["상사B(주)"]})
x2 = dt.prepare_dmf(D, ap)
ok(bool(x2.loc[x2["신청인"] == "상사B", "오리지널사"].all()), "허가자료 신약 회사와 같은 이름이면 오리지널사")

# ── 임상 ──
C = pd.DataFrame({
    "성분명": ["가성분염산염", "가성분", "AB-101", "AB-101", "CD-202", "가성분, 라성분"],
    "CLINIC_STEP_NM": ["3상", "생동", "생동", "1상", "생동", "생동"],
    "CLST_APRV_DT": ["20250101", "2026-02-01", "20260701", "20260702", "20260801", "20260301"],
    "TRGT_DISS_NM": ["주요 우울증", "주요우울증", "우울증", "우울증", "본태성 고혈압", "우울증, 불면"],
    "CLNC_TEST_TITLE": ["", "", "“AB-101”과 “R”의 생동, 2군, 4기", "PK", "x", ""],
    "제품명": ["a", "b", "AB-101", "AB-101", "CD-202", "c"],
})
cx = dt.prepare_clinical(C)
ok(cx["코드명"].tolist() == [False, False, True, True, True, False], "코드명 판별")
terms, nev = dt.ingredient_terms(cx, "가성분")
ok(terms[:1] == ["우울증"] and nev == 3, "성분 질환 핵심어 = 우울증 (근거 3건)")
ok(dt.named_recent(cx, "가성분", "2026-11-14") == 2, "성분 이름이 적힌 최근 생동·1상 2건")
sp = dt.suspects(cx, terms, "2026-01-01")
ok(sp["코드"].tolist() == ["AB-101", "AB-101"], "우울증 코드명 의심 2건(고혈압 CD-202는 빠짐)")
ok(any("4기" in c for c in sp["단서"]), "4기 설계 단서")
canon, raw = dt.parse_terms("고혈압")
ok(dt.suspects(cx, canon, "2026-01-01", raw=raw)["코드"].tolist() == ["CD-202"], "직접 넣은 핵심어로 찾기")
ok(dt.suspects(cx, [], "2026-01-01").empty, "핵심어 없으면 빈 표")

# ── 판정 ──
T = "2026-11-14"
ok(dt.stage({"원료사_증감": 3, "추가_증감": 0, "성분생동1상": 0, "코드명의심": 0, "물질만료": pd.Timestamp("2030-01-01")}, T) == "원료 진입기", "판정: 원료 진입기")
ok(dt.stage({"원료사_증감": 3, "추가_증감": 0, "성분생동1상": 0, "코드명의심": 1, "물질만료": None}, T) == "개발 진행기", "판정: 개발 진행기")
ok(dt.stage({"원료사_증감": 1, "추가_증감": 2, "성분생동1상": 3, "코드명의심": 0, "물질만료": pd.Timestamp("2027-03-01")}, T) == "출시 임박", "판정: 출시 임박")
ok(dt.stage({"원료사_증감": -2, "추가_증감": 0, "성분생동1상": 0, "코드명의심": 0, "물질만료": pd.Timestamp("2020-01-01")}, T) == "지나감", "판정: 지나감")

print("\n" + (f"문제 {len(bad)}건" if bad else "전부 정상"))
sys.exit(1 if bad else 0)
