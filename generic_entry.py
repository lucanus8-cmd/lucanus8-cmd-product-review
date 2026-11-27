"""약가 조회 · 제네릭 등재 시점 (화면 없이 계산만 — 시험하기 쉽게 따로 둔다).

규칙 (2026-11 확정, 72개 성분 시험)
1) 성분(복합제는 성분 조합, 순서 무관)으로 허가 자료에서 허가일자가 가장 빠른 품목 = 오리지널.
   더 늦게 허가됐어도 '신약' 표시가 있으면 그 품목. 사용자가 다른 품목으로 바꿀 수 있다.
2) 오리지널과 같은 회사·같은 상표의 모든 함량을 함께 본다.
3) 허가 품목명과 약가 제품명을 '정리한 이름'(규격·괄호·띄어쓰기 떼고 단위 통일)으로 맞춘다.
4) ◆ 같은 약가코드 = 오리지널과 주성분코드가 같은 다른 제품의 첫 급여 등재일.
   ◇ 염변경 등 = 같은 성분(허가 기준)인데 약가코드가 다른 제품.
   오리지널 자기 제품(같은 상표)은 뺀다.
5) 약가 이력에 수천 품목이 한꺼번에 처음 나온 날(자료 묶음)에 처음 나온 제품은 실제 등재일을 모른다.
6) 주의문구: 오리지널이 자료 묶음 날짜에 이미 있던 오래된 약일 때만, 그날 상한가를 같은 함량·제형끼리
   비교해 오리지널보다 5% 넘게 비싼 품목이 있으면 알린다 (자동으로 바꾸지는 않음).
"""
import re

import pandas as pd

_SPLIT = r"[|/,+]|및"
_UNITS = (("마이크로그램", "mcg"), ("마이크로그람", "mcg"), ("밀리그램", "mg"), ("밀리그람", "mg"),
          ("㎎", "mg"), ("㎍", "mcg"), ("μg", "mcg"), ("밀리리터", "ml"), ("㎖", "ml"),
          ("그램", "g"), ("그람", "g"), ("·", ""), (".0mg", "mg"))
_FORM_TAIL = re.compile(r"(서방|장용|구강붕해|필름코팅|연질|경질)?(정|캡슐|주|시럽|산|과립|액|현탁액|패취|겔|크림)$")
BULK_MIN = 3000          # 하루에 이만큼 넘게 처음 나온 날 = 자료 묶음 날짜
WARN_RATIO = 1.05        # 주의문구: 오리지널보다 5% 넘게 비쌀 때


def nn(x):
    """제품명 정리: 규격(_(…)) 떼기 · 괄호 속 성분명 떼기 · 띄어쓰기 없애기 · 단위 표기 통일."""
    t = str(x).split("_(")[0]
    t = re.sub(r"\([^)]*\)", "", t)
    t = re.sub(r"\s+", "", t).lower()
    for a, b in _UNITS:
        t = t.replace(a, b)
    return t


def brand(name):
    """상표 줄기: '펠루비정(펠루비프로펜)' → '펠루비', '리피토정10밀리그램' → '리피토'."""
    t = re.sub(r"\s+", "", str(name))
    t = re.split(r"[0-9(_\[]", t)[0]
    return _FORM_TAIL.sub("", t).lower()


def strength(name):
    """함량: 이름에서 '5mg', '40/10mg'. 이름에 없으면 규격 _(…) 에서."""
    m = re.search(r"(\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)*)(mg|mcg|g|ml)", nn(name))
    if m:
        return m.group(1) + m.group(2)
    sp = re.search(r"_\(([^)]*)\)", str(name))
    if sp:
        t = nn("x" + sp.group(1))[1:]
        m = re.search(r"(\d+(?:\.\d+)?)(mg|mcg|g|ml)", t)
        if m:
            return m.group(1) + m.group(2)
    return ""


def cores_of(s, ing_core):
    """허가 '주성분' 글자 → 성분 코어 묶음 (염·수화물 표기는 ing_core 가 뗀다)."""
    parts = [p for p in re.split(_SPLIT, str(s or "")) if p.strip()]
    return frozenset(c for c in (ing_core(p) for p in parts) if c)


def want_of(term, ing_core):
    """화면에서 고른 성분 '텔미사르탄 + 암로디핀' → 코어 묶음."""
    return frozenset(c for c in (ing_core(x) for x in str(term).split("+")) if c)


def _match(cs, want):
    return len(cs) == len(want) and all(any(c == w or c.startswith(w) for c in cs) for w in want)


def _code(x):
    x = x.strip().upper() if isinstance(x, str) else ""
    return "" if x in ("NAN", "NONE", "NULL") else x


def prep_price(pr):
    """약가 이력에서 이 계산에 필요한 열만 추려 정리 이름·약가코드를 붙인다."""
    keep = [c for c in ["제품코드", "제품명", "업체명", "적용일자", "급여구분", "금액", "주성분코드", "제제코드"]
            if c in pr.columns]
    p = pr[keep].copy()
    names = p["제품명"].astype(str)
    uniq = pd.Series(names.unique())
    p["_nn"] = names.map(dict(zip(uniq, uniq.map(nn))))
    cd = p["주성분코드"].map(_code) if "주성분코드" in p.columns else pd.Series("", index=p.index)
    alt = p["제제코드"].map(_code) if "제제코드" in p.columns else pd.Series("", index=p.index)
    p["_cd"] = cd.where(cd.str.len() > 0, alt)
    p["_pc"] = p["제품코드"].astype(str).str.strip()
    p["적용일자"] = pd.to_datetime(p["적용일자"], errors="coerce")
    p["금액"] = pd.to_numeric(p["금액"], errors="coerce")
    return p.drop(columns=[c for c in ("주성분코드", "제제코드") if c in p.columns])


def bulk_dates(p):
    vc = p.groupby("제품명")["적용일자"].min().value_counts()
    return set(vc[vc >= BULK_MIN].index)


def prep_approval(ap, ing_core, ap_date):
    keep = [c for c in ["품목기준코드", "품목명", "업체명", "허가일자", "주성분", "신약구분", "상태"] if c in ap.columns]
    a = ap[keep].copy()
    a["_d"] = ap_date(a)
    uniq = pd.Series(a["주성분"].astype(str).unique())
    a["_cores"] = a["주성분"].astype(str).map(dict(zip(uniq, uniq.map(lambda s: cores_of(s, ing_core)))))
    a["_br"] = a["품목명"].map(brand)
    a["_nn"] = a["품목명"].map(nn)
    return a


def ingredient_options(a):
    """성분 선택 목록: 단일 성분 + 복합제 조합('A + B', 가나다순)."""
    singles, combos = set(), set()
    for cs in a["_cores"].unique():
        cs = sorted(c for c in cs if len(c) >= 2)
        if len(cs) == 1:
            singles.add(cs[0])
        elif 2 <= len(cs) <= 4:
            combos.add(" + ".join(cs))
    return sorted(singles) + sorted(combos)


def analyze(p, a, bulk, term, ing_core, pick=None):
    """성분 하나에 대한 오리지널·제네릭 등재 계산.

    pick: 사용자가 고른 오리지널 (상표, 업체명). None 이면 자동.
    반환 dict: families(선택 목록), auto, chosen, reason, orig_codes(제품코드), codes(약가코드),
               same(◆ 표), salt(◇ 표), warn(주의 후보), n_combo(빼낸 복합제 수), note
    """
    want = want_of(term, ing_core)
    out = {"families": [], "auto": None, "chosen": None, "reason": "", "orig_codes": [], "codes": [],
           "same": pd.DataFrame(), "salt": pd.DataFrame(), "warn": [], "n_combo": 0, "note": ""}
    if not want:
        out["note"] = "성분을 알 수 없음"
        return out
    hit = a[a["_cores"].map(lambda cs: _match(cs, want)) & a["_d"].notna()].sort_values("_d")
    if hit.empty:
        out["note"] = "허가 자료에서 이 성분 품목을 찾지 못함"
        return out
    # 상표·회사 묶음 (오리지널 선택 목록) — 허가일 순
    fam = (hit.groupby(["_br", "업체명"], sort=False)
              .agg(d=("_d", "min"), name=("품목명", "first"),
                   new=("신약구분", lambda s: bool((s.astype(str).str.strip() == "신약").any())))
              .reset_index().sort_values("d"))
    out["families"] = [(r["_br"], r["업체명"], r["name"], r["d"], r["new"]) for _, r in fam.iterrows()]
    auto = fam.iloc[0]
    reason = "최초 허가"
    newf = fam[fam["new"]]
    if not newf.empty and (newf.iloc[0]["_br"], newf.iloc[0]["업체명"]) != (auto["_br"], auto["업체명"]):
        auto, reason = newf.iloc[0], "신약 표시"
    elif bool(auto["new"]):
        reason = "신약 표시"
    out["auto"] = (auto["_br"], auto["업체명"])
    chosen = tuple(pick) if pick else out["auto"]
    out["chosen"] = chosen
    out["reason"] = "직접 선택" if pick and tuple(pick) != out["auto"] else reason

    ob, oco = chosen
    items = hit[(hit["_br"] == ob) & (hit["업체명"] == oco)]
    orow = p[p["_nn"].isin(set(items["_nn"]))]
    if orow.empty:
        out["note"] = "약가(급여) 자료에서 오리지널을 찾지 못함 — 비급여이거나 이름이 다를 수 있음"
        return out
    e = set(orow["_pc"])
    codes = sorted({c for c in orow["_cd"] if c})
    out["orig_codes"], out["codes"] = sorted(e), codes

    gy = p[p["급여구분"] == "급여"]
    last = p.sort_values("적용일자").drop_duplicates("_pc", keep="last").set_index("_pc")
    now = last["금액"].where(last["급여구분"] == "급여")

    def table(rows, kind):
        rows = rows[~rows["_pc"].isin(e) & (rows["_nn"].map(brand) != ob)]
        if rows.empty:
            return pd.DataFrame()
        g = rows.sort_values("적용일자")
        first = g.groupby("제품명").agg(등재일=("적용일자", "min"), 업체=("업체명", "first"),
                                     _pc=("_pc", "last"), 첫상한가=("금액", "first")).reset_index()
        first["지금상한가"] = first["_pc"].map(now)
        first["날짜모름"] = first["등재일"].isin(bulk)
        first["구분"] = kind
        return first.sort_values("등재일").drop(columns=["_pc"])

    out["same"] = table(gy[gy["_cd"].isin(codes)], "◆")
    same_names = set(hit["_nn"])
    out["salt"] = table(gy[gy["_nn"].isin(same_names) & ~gy["_cd"].isin(codes)], "◇")

    if len(want) == 1:      # 단일제를 볼 때 빼낸 복합제 수 (이 성분이 든 복합제 품목)
        combo = a[a["_cores"].map(lambda cs: len(cs) > 1 and any(any(c == w or c.startswith(w) for c in cs)
                                                                   for w in want))]
        out["n_combo"] = int(p[p["_nn"].isin(set(combo["_nn"]))]["_pc"].nunique()) if not combo.empty else 0

    # 주의문구 (오래된 오리지널만, 자료 시작일 가격으로 같은 함량·제형 비교)
    og = gy[gy["_pc"].isin(e)]
    d0 = og["적용일자"].min() if not og.empty else None
    if d0 is not None and d0 in bulk:
        names = {n: (nm, co, d) for n, nm, co, d in zip(hit["_nn"], hit["품목명"], hit["업체명"], hit["_d"])}
        snap = gy[(gy["적용일자"] == d0) & gy["_nn"].isin(names.keys())].copy()
        if not snap.empty:
            snap["_st"] = snap["제품명"].map(strength)
            snap["_fm"] = snap["_cd"].str[-3:]
            osn = snap[snap["_pc"].isin(e) & (snap["_st"] != "")]
            cand = snap[~snap["_pc"].isin(e) & (snap["_nn"].map(brand) != ob)]
            warn = {}
            for (st_, fm), g in osn.groupby(["_st", "_fm"]):
                op = g["금액"].max()
                hi = cand[(cand["_st"] == st_) & (cand["_fm"] == fm) & (cand["금액"] > op * WARN_RATIO)]
                for _, h in hi.iterrows():
                    nm, co, d = names[h["_nn"]]
                    key = (brand(nm), co)
                    if key not in warn or h["금액"] > warn[key]["price"]:
                        warn[key] = {"brand": key[0], "company": co, "name": nm, "approved": d,
                                     "strength": st_, "price": float(h["금액"]), "orig_price": float(op)}
            out["warn"] = sorted(warn.values(), key=lambda w: (-w["price"], w["approved"]))
    return out
