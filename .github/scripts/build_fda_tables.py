"""FDA 공개 자료로 두 표를 만든다. 사이트와 상관없다(사이트 코드는 읽지도 고치지도 않는다).

① 성분 이름 대조표 (fda_substance_names): FDA 성분 사전(GSRS)의 공식 이름(INN·USAN·JAN·USP 등)만 추려,
   같은 성분(염·용매화물은 모체로 묶음)끼리 대표 이름(INN 우선)과 WHO ATC 분류를 붙인다.
② FDA 허가 목록 + 적응증 (fda_products): Drugs@FDA 전 제품에 제형 묶음·주성분(대표 이름)·첫 허가일과
   신청(NDA·BLA)별 라벨 적응증(INDICATIONS AND USAGE 앞부분)을 붙인다.

결과: out/fda/*.csv (로봇용) · *.xlsx (보기용) · fda_meta.json (각 자료의 FDA 기준일)
공개 자료만 다룬다. 매출 자료와 상관없다.
"""
import io
import json
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from check_overseas import FORM_EN, en_moiety, form_of, get  # noqa: E402

OUT = os.environ.get("OUT_DIR", "out/fda")
OFFICIAL = re.compile(r"\[(INN|USAN|JAN|BAN|WHO-DD|USP|MI|EP|JP|DCF|MART\.|VANDF|HSDB)[^\]]*\]\s*$", re.I)


def index():
    return json.loads(get("https://api.fda.gov/download.json", timeout=60))["results"]


def stream(parts):
    import ijson
    for p in parts:
        z = zipfile.ZipFile(io.BytesIO(get(p["file"], timeout=900)))
        for fn in z.namelist():
            with z.open(fn) as fp:
                yield from ijson.items(fp, "results.item")


# ═══════════ ① 성분 이름 대조표 ═══════════
def build_names(idx):
    t0 = time.time()
    rows = defaultdict(lambda: {"tags": set(), "ex": ""})
    inn, usan, atc = {}, {}, defaultdict(set)
    n = 0
    for r in stream(idx["other"]["substance"]["partitions"]):
        n += 1
        u = r.get("unii")
        if not u:
            continue
        grp = next(((rel.get("related_substance") or {}).get("unii") for rel in r.get("relationships") or []
                    if rel.get("type") == "SALT/SOLVATE->PARENT"), None) or u
        for cd in r.get("codes") or []:
            cs = (cd.get("code_system") or cd.get("codeSystem") or "").upper()
            c0 = (cd.get("code") or "").strip().upper()
            if "ATC" in cs and re.fullmatch(r"[A-Z]\d\d[A-Z]{0,2}\d{0,2}", c0):
                atc[grp].add(c0)
        for nm in r.get("names") or []:
            raw = nm.get("name") or ""
            tag = OFFICIAL.search(raw)
            if not tag:
                continue
            plain = OFFICIAL.sub("", raw).strip()
            k = en_moiety(plain, syn=False)
            if not k or len(k) > 60:
                continue
            t = tag.group(1).upper()
            x = rows[(k, grp)]
            x["tags"].add(t)
            x["ex"] = x["ex"] or plain
            if t == "INN":
                inn.setdefault(grp, k)
            elif t == "USAN":
                usan.setdefault(grp, k)
    rep = {g: inn.get(g) or usan.get(g) or "" for g in {g for _, g in rows}}
    df = pd.DataFrame([{"이름(염 뗀)": k, "대표 이름": rep.get(g, ""), "성분 묶음(UNII)": g,
                        "공식 표기 예": x["ex"], "꼬리표": ",".join(sorted(x["tags"])),
                        "WHO ATC": ",".join(sorted(atc.get(g, ())))} for (k, g), x in rows.items()])
    df = df.sort_values(["이름(염 뗀)", "대표 이름"]).reset_index(drop=True)
    pairs = defaultdict(set)
    for g in set(inn) & set(usan):
        if inn[g] != usan[g]:
            pairs[usan[g]].add(inn[g])
    pr = pd.DataFrame(sorted((u, next(iter(i))) for u, i in pairs.items() if len(i) == 1), columns=["미국 이름(USAN)", "국제 이름(INN)"])
    print(f"① 성분 {n:,}개 → 공식 이름 {len(df):,}줄 · 대표 이름 있는 묶음 {sum(1 for v in rep.values() if v):,}"
          f" · ATC 있는 묶음 {len(atc):,} · 미국→국제 이름 짝 {len(pr):,} · {time.time() - t0:.0f}초")
    return df, pr


def make_canon(names, pairs):
    """대조표로 '영문 성분명 → 대표 이름' 함수를 만든다 (점검 로봇과 같은 규칙)."""
    u2i = dict(zip(pairs["미국 이름(USAN)"], pairs["국제 이름(INN)"]))
    by = defaultdict(set)
    rep = {}
    for k, g, r in zip(names["이름(염 뗀)"], names["성분 묶음(UNII)"], names["대표 이름"]):
        by[k].add(g)
        if r:
            rep[g] = r
    stats = Counter()

    def canon(x):
        k = en_moiety(x, syn=False)
        if k in u2i:
            stats["미국명→국제명"] += 1
            return u2i[k]
        g = by.get(k)
        if g and len(g) == 1 and rep.get(next(iter(g))):
            stats["대표 이름"] += 1
            return rep[next(iter(g))]
        if g:
            reps = {rep[x] for x in g if rep.get(x)}
            if k in reps:
                stats["여러 묶음·대표와 같음"] += 1
                return k
            if len(reps) == 1:
                stats["여러 묶음·대표 하나"] += 1
                return next(iter(reps))
        stats["그대로"] += 1
        return k
    return canon, stats


# ═══════════ ② FDA 허가 목록 + 적응증 ═══════════
def build_products(idx, canon):
    t0 = time.time()
    rows = []
    for a in stream(idx["drug"]["drugsfda"]["partitions"]):
        no = a.get("application_number", "")
        kind = "BLA" if no.startswith("BLA") else ("ANDA" if no.startswith("ANDA") else "NDA")
        orig = [s.get("submission_status_date", "") for s in a.get("submissions") or []
                if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP"]
        of = a.get("openfda") or {}
        for pr in a.get("products") or []:
            ings = [x.get("name", "") for x in pr.get("active_ingredients") or []]
            parts = [y for x in ings for y in re.split(r"\s+AND\s+|;", x) if y.strip()]
            d = min(orig) if orig else ""
            rows.append({
                "신청번호": no, "구분": kind, "상표": pr.get("brand_name", ""), "회사": a.get("sponsor_name", ""),
                "성분(원문)": " ; ".join(ings),
                "함량": " ; ".join(f"{x.get('strength', '')}" for x in pr.get("active_ingredients") or []),
                "주성분(대표 이름)": " + ".join(sorted({canon(x) for x in parts})),
                "제형(원문)": pr.get("dosage_form", ""), "투여경로": pr.get("route", ""),
                "제형 묶음": form_of(f"{pr.get('dosage_form', '')} {pr.get('route', '')}", FORM_EN) or "",
                "판매 상태": pr.get("marketing_status", ""), "대조약(RLD)": pr.get("reference_drug", ""),
                "동등성 코드(TE)": pr.get("te_code", ""),
                "첫 허가일": f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else "",
                "약효 분류(FDA EPC)": " ; ".join(of.get("pharm_class_epc") or [])[:200]})
    df = pd.DataFrame(rows)
    print(f"② Drugs@FDA 제품 {len(df):,}개 · 신청 {df['신청번호'].nunique():,}건 · {time.time() - t0:.0f}초")

    # 적응증: 처음에는 라벨 전체 파일에서 한 번에 (이후에는 바뀐 라벨만 API 로 덧붙일 예정)
    t0 = time.time()
    want = set(df.loc[df["구분"] != "ANDA", "신청번호"])
    ind, eff = {}, {}
    for r in stream(idx["drug"]["label"]["partitions"]):
        apps = (r.get("openfda") or {}).get("application_number") or []
        hit = [a for a in apps if a in want]
        if not hit:
            continue
        txt = " ".join(r.get("indications_and_usage") or [])
        txt = re.sub(r"\s+", " ", re.sub(r"^\s*\d*\.?\s*INDICATIONS?\s*(AND|&)\s*USAGE\s*", "", txt, flags=re.I)).strip()
        et = r.get("effective_time", "")
        for a in hit:
            if txt and et >= eff.get(a, ""):          # 같은 신청에 라벨이 여럿이면 가장 최근 것
                ind[a], eff[a] = txt[:600], et
    df["미국 적응증(라벨)"] = df["신청번호"].map(ind).fillna("")
    df["라벨 기준일"] = df["신청번호"].map(lambda a: f"{eff[a][:4]}-{eff[a][4:6]}-{eff[a][6:8]}" if a in eff else "")
    print(f"   적응증 붙은 신청 {len(ind):,} / {len(want):,} (NDA·BLA) · {time.time() - t0:.0f}초")
    return df


PKEY = ["신청번호", "상표", "제형(원문)", "투여경로", "함량"]


def changes(prev_dir, prod, names, today):
    """직전 파일과 비교한 변경 내역 (새 허가 · 목록에서 빠짐 · 판매 상태 변경 · 적응증 변경 · 새 성분 이름)"""
    pp, pn = f"{prev_dir}/fda_products.csv", f"{prev_dir}/fda_substance_names.csv"
    if not os.path.exists(pp):
        print("   직전 파일 없음 — 이번이 처음이라 변경 내역은 다음부터 생깁니다")
        return pd.DataFrame(columns=["날짜", "구분", "신청번호", "상표", "주성분", "이전", "이후"])
    old = pd.read_csv(pp, dtype=str, keep_default_na=False)
    new = prod.astype(str)
    o = old.set_index(PKEY, drop=False)
    n = new.set_index(PKEY, drop=False)
    o = o[~o.index.duplicated()]
    n = n[~n.index.duplicated()]
    out = []

    def add(kind, r, before="", after=""):
        out.append({"날짜": today, "구분": kind, "신청번호": r["신청번호"], "상표": r["상표"],
                    "주성분": r.get("주성분(대표 이름)", ""), "이전": before, "이후": after})
    for k in n.index.difference(o.index):
        r = n.loc[k]
        add("새 제품", r, "", f"{r['제형(원문)']} {r['함량']} · {r['판매 상태']} · 첫 허가 {r['첫 허가일']}")
    for k in o.index.difference(n.index):
        add("목록에서 빠짐", o.loc[k], f"{o.loc[k]['판매 상태']}", "")
    both = n.index.intersection(o.index)
    st = (o.loc[both, "판매 상태"] != n.loc[both, "판매 상태"])
    for k in st[st].index:
        add("판매 상태 변경", n.loc[k], o.loc[k, "판매 상태"], n.loc[k, "판매 상태"])
    oi = old.drop_duplicates("신청번호").set_index("신청번호")["미국 적응증(라벨)"]
    ni = new.drop_duplicates("신청번호").set_index("신청번호")
    for a in ni.index.intersection(oi.index):
        if ni.loc[a, "미국 적응증(라벨)"] and ni.loc[a, "미국 적응증(라벨)"] != oi.loc[a]:
            add("적응증 변경", ni.loc[a].reindex(["신청번호", "상표", "주성분(대표 이름)"]).fillna("").to_dict() | {"신청번호": a},
                oi.loc[a][:300], ni.loc[a, "미국 적응증(라벨)"][:300])
    if os.path.exists(pn):
        on = set(pd.read_csv(pn, dtype=str, keep_default_na=False)["이름(염 뗀)"])
        for k, r in zip(names["이름(염 뗀)"], names["대표 이름"]):
            if k not in on:
                out.append({"날짜": today, "구분": "새 성분 이름", "신청번호": "", "상표": "", "주성분": r or k,
                            "이전": "", "이후": k})
    ch = pd.DataFrame(out)
    print(f"   변경 내역 {len(ch):,}건: {dict(Counter(ch['구분'])) if len(ch) else {}}")
    return ch


def main():
    os.makedirs(OUT, exist_ok=True)
    idx = index()
    meta = {"만든 날(UTC)": time.strftime("%Y-%m-%d %H:%M"),
            "성분 사전 기준일": idx["other"]["substance"].get("export_date"),
            "Drugs@FDA 기준일": idx["drug"]["drugsfda"].get("export_date"),
            "라벨 기준일": idx["drug"]["label"].get("export_date")}
    names, pairs = build_names(idx)
    canon, stats = make_canon(names, pairs)
    prod = build_products(idx, canon)
    print("   주성분 이름 정리:", dict(stats))
    names.to_csv(f"{OUT}/fda_substance_names.csv", index=False, encoding="utf-8-sig")
    pairs.to_csv(f"{OUT}/fda_usan_inn_pairs.csv", index=False, encoding="utf-8-sig")
    prod.to_csv(f"{OUT}/fda_products.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(f"{OUT}/FDA_성분_이름_대조표.xlsx") as w:
        names.to_excel(w, sheet_name="공식 이름", index=False)
        pairs.to_excel(w, sheet_name="미국명→국제명", index=False)
    with pd.ExcelWriter(f"{OUT}/FDA_허가목록_적응증.xlsx") as w:
        prod.to_excel(w, sheet_name="FDA 허가 제품", index=False)
    meta.update({"공식 이름 줄": len(names), "미국→국제 짝": len(pairs), "FDA 제품": len(prod)})
    json.dump(meta, open(f"{OUT}/fda_meta.json", "w"), ensure_ascii=False, indent=1)
    for f in sorted(os.listdir(OUT)):
        print(f"   {f}: {os.path.getsize(f'{OUT}/{f}') / 1e6:.1f}MB")
    print(json.dumps(meta, ensure_ascii=False))
    print("예시 — 대조표:", names[names["이름(염 뗀)"].isin(["albuterol", "salbutamol", "amlodipine", "indomethacin"])].head(8).to_dict("records"))
    print("예시 — 허가 목록:", prod[prod["상표"].isin(["TYMLOS", "VIZZ"])].head(3).to_dict("records"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
