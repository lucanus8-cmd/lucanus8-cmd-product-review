"""매출 분석 v2 DuckDB(필요한 줄만 꺼냄) vs 예전(표 전체 pkl + 거르기) — 자료 수준에서 똑같은 표인지 대조.
사이트에 쓰지 않는다. 매출 숫자는 적지 않는다(같음/다름 · 줄 수만). 제품·성분 이름은 공개 정보라 조건 이름으로만 적는다."""
import os, re, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd()
sys.path[:0] = [os.path.join(ROOT, "v2"), ROOT]
import pandas as pd  # noqa: E402
import streamlit  # noqa: E402,F401
from core import data  # noqa: E402
from core import sales_rows as SR  # noqa: E402
from dose_util import fix_iqvia_dose, iqvia_combo, ubist_combo, price_ingredient_counts, dose_match  # noqa: E402

src = open(os.path.join(ROOT, "v2/views/sales_body.py"), encoding="utf-8").read()
exec(src[src.index("def make_filter_mask"):src.index("def build_excel")])   # 화면 코드의 거르기 함수 그대로

def rss():
    with open("/proc/self/status") as f:
        for ln in f:
            if ln.startswith("VmRSS"):
                return int(ln.split()[1]) / 1024

N = {"같음": 0, "다름": 0}
def eq(a, b, lab):
    try:
        pd.testing.assert_frame_equal(a, b, check_dtype=True, check_index_type=False)
        N["같음"] += 1; print(f"같음 · {lab} · {len(a):,}줄", flush=True)
    except AssertionError as e:
        N["다름"] += 1; print(f"다름 · {lab} · 새 {len(a):,}줄 / 예전 {len(b):,}줄 · {str(e).splitlines()[0][:160]}", flush=True)

for k in ("iqvia", "ubist"):
    print(k, "파일:", data.ensure(k))

t = time.time(); base = rss()
I = pd.read_pickle(data.SAVED / "iqvia.pkl"); I, _ = fix_iqvia_dose(I); I["_구성"] = iqvia_combo(I)
code_n = price_ingredient_counts(data.SD / "price.parquet")
U = pd.read_pickle(data.SAVED / "ubist.pkl"); U["_구성"] = ubist_combo(U, code_n)
print(f"예전 방식으로 두 표 올림: +{rss()-base:.0f} MB · {time.time()-t:.1f}초 · IQVIA {len(I):,}줄 · UBIST {len(U):,}줄")
print("IQVIA 칸 형식:", dict(pd.Series([str(x) for x in I.dtypes]).value_counts()), "· index:", type(I.index).__name__)
print("UBIST 칸 형식:", dict(pd.Series([str(x) for x in U.dtypes]).value_counts()), "· index:", type(U.index).__name__)
print("구성:", I["_구성"].value_counts().to_dict(), U["_구성"].value_counts().to_dict())

eq(SR.rows("iqvia"), I, "IQVIA 전체")
eq(SR.rows("ubist"), U, "UBIST 전체")
for c in (("단일제",), ("복합제",)):
    eq(SR.rows("iqvia", combo=c), I[I["_구성"].isin(c)], f"IQVIA 전체 {c}")
    eq(SR.rows("ubist", combo=c), U[U["_구성"].isin(c)], f"UBIST 전체 {c}")

MAPI = {"제품명": "제품명", "성분명": "성분명", "제조사": "회사명", "용량": "용량", "ATC": "ATC 4(한글)",
        "ETC/OTC": "ETC/OTC", "급여구분": "급여구분"}
UATC = next((c for c in U.columns if "ATC" in str(c).upper()), None)
MAPU = {"제품명": "제품명", "성분명": "성분", "제조사": "제조사명", "용량": "용량", "ATC": UATC, "급여구분": "급여구분"}

# 실제 자료에서 값 고르기 (많이 나오는 것 위주)
def top(df, col, n):
    return [str(x) for x in df[col].dropna().astype(str).value_counts().head(n).index] if col in df.columns else []
ip, ic, im, ia = top(I, "제품명", 3), top(I, "성분명", 3), top(I, "회사명", 2), top(I, "ATC 4(한글)", 3)
cases_i = [[{"field": "제품명", "value": [p]}] for p in ip] + \
          [[{"field": "제품명", "value": "리피토"}], [{"field": "제품명", "value": "트윈"}], [{"field": "제품명", "value": "정"}],
           [{"field": "성분명", "value": ic[0][:6]}], [{"field": "성분명", "value": "+"}], [{"field": "제조사", "value": im[0][:4]}],
           [{"field": "용량", "value": "40"}], [{"field": "용량", "value": "40/10"}], [{"field": "제품명", "value": "트윈"}, {"field": "용량", "value": "40/5"}],
           [{"field": "ATC", "value": ia[:2]}], [{"field": "ETC/OTC", "value": ["OTC"]}], [{"field": "급여구분", "value": "급여"}],
           [{"field": "제품명", "value": "리피.*"}], [{"field": "성분명", "value": "na"}]]
for c in cases_i:
    for combo in (None, ("단일제",), ("복합제",)):
        for need in (False, True):
            w, p = SR.prefilter(c, MAPI, SR.columns("iqvia"))
            a = SR.rows("iqvia", w, p, combo=combo)
            a = a[make_filter_mask(a, c, MAPI, require_any=need)]
            b0 = I if not combo else I[I["_구성"].isin(combo)]
            eq(a, b0[make_filter_mask(b0, c, MAPI, require_any=need)], f"IQVIA {c} {combo or ''} {'' if need else '(대시보드)'}")
up, uc, um = top(U, "제품명", 3), top(U, "성분", 2), top(U, "제조사명", 2)
cases_u = [[{"field": "제품명", "value": [p]}] for p in up] + \
          [[{"field": "제품명", "value": "리피토"}], [{"field": "성분명", "value": "metformin"}], [{"field": "성분명", "value": uc[0][:8]}],
           [{"field": "제조사", "value": um[0][:3]}], [{"field": "용량", "value": "10"}], [{"field": "ATC", "value": top(U, UATC, 2)}],
           [{"field": "제품명", "value": "정"}, {"field": "용량", "value": "5"}], [{"field": "급여구분", "value": "급여"}]]
for c in cases_u:
    for combo in (None, ("단일제",), ("복합제",)):
        w, p = SR.prefilter(c, MAPU, SR.columns("ubist"))
        a = SR.rows("ubist", w, p, combo=combo)
        a = a[make_filter_mask(a, c, MAPU, require_any=False)]
        b0 = U if not combo else U[U["_구성"].isin(combo)]
        eq(a, b0[make_filter_mask(b0, c, MAPU, require_any=False)], f"UBIST {c} {combo or ''}")

# 시장 분석: ATC 하나 고르기
for atc in ia[:3]:
    for combo in (None, ("단일제",)):
        b0 = I if not combo else I[I["_구성"].isin(combo)]
        eq(SR.rows("iqvia", 'CAST("ATC 4(한글)" AS VARCHAR) = ?', [atc], combo=combo),
           b0[b0["ATC 4(한글)"].astype(str) == atc].copy(), f"시장 IQVIA ATC {atc} {combo or ''}")
for atc in top(U, UATC, 2):
    eq(SR.rows("ubist", f'CAST("{UATC}" AS VARCHAR) = ?', [atc]), U[U[UATC].astype(str) == atc].copy(), f"시장 UBIST ATC {atc}")
# IQVIA vs UBIST 비교: 제품 하나
for pk in ip[:2] + ["리피토정"]:
    eq(SR.rows("iqvia", 'CAST("제품명" AS VARCHAR) = ?', [pk]), I[I["제품명"].astype(str) == pk], f"비교 IQVIA {pk}")
    eq(SR.rows("ubist", 'contains(CAST("제품명" AS VARCHAR), ?)', [pk]),
       U[U["제품명"].astype(str).str.contains(re.escape(pk), na=False)], f"비교 UBIST {pk}")
# 성장 제품: 칸 일부
ly = [c for c in I.columns if re.match(r"\d{4}년$", c)]
need = [c for c in ["제품명", "회사명", "성분명", "ATC 4(한글)"] if c in I.columns] + ly
eq(SR.rows("iqvia", cols=need), I[need + ["_구성"]], "성장 IQVIA 칸 일부")
# 목록
for col in ("제품명", "ATC 4(한글)"):
    ok = SR.distinct("iqvia", col, SR.mtime("iqvia")) == sorted(I[col].dropna().astype(str).unique().tolist())
    print(("같음" if ok else "다름"), "· 목록 IQVIA", col); N["같음" if ok else "다름"] += 1
for col in ("제품명", UATC):
    ok = SR.distinct("ubist", col, SR.mtime("ubist")) == sorted(U[col].dropna().astype(str).unique().tolist())
    print(("같음" if ok else "다름"), "· 목록 UBIST", col); N["같음" if ok else "다름"] += 1
c1 = ("단일제",)
ok = SR.distinct("iqvia", "ATC 4(한글)", SR.mtime("iqvia"), c1) == sorted(I[I["_구성"].isin(c1)]["ATC 4(한글)"].dropna().astype(str).unique().tolist())
print(("같음" if ok else "다름"), "· 목록 IQVIA ATC (단일제만)"); N["같음" if ok else "다름"] += 1
# UBIST 덜 찬 해
by = {}
for c in U.columns:
    m = re.match(r"처방조제액\(원\)_(\d{4})Q([1-4])$", c)
    if m and float(pd.to_numeric(U[c], errors="coerce").fillna(0).sum()) != 0:
        by.setdefault(m.group(1), set()).add(int(m.group(2)))
old_part = {y: sorted(q) for y, q in by.items() if len(q) < 4}
qc = tuple(c for c in U.columns if re.match(r"처방조제액\(원\)_\d{4}Q[1-4]$", c))
sm = SR.col_sums("ubist", qc, SR.mtime("ubist"))
by2 = {}
for c in qc:
    m = re.match(r"처방조제액\(원\)_(\d{4})Q([1-4])$", c)
    if sm.get(c, 0) != 0:
        by2.setdefault(m.group(1), set()).add(int(m.group(2)))
ok = old_part == {y: sorted(q) for y, q in by2.items() if len(q) < 4}
print(("같음" if ok else "다름"), "· UBIST 덜 찬 해", old_part); N["같음" if ok else "다름"] += 1
print(f"══ 같음 {N['같음']} · 다름 {N['다름']}")
