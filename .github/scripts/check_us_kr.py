"""미국 FDA 허가(판매 중)와 국내 허가(정상, 전문+일반)를 '주성분(염 무시) + 제형'으로 맞춰 본다.
사이트와 상관없다. 공개 자료만 읽고 건수·예시만 적는다(매출 없음).

성분 이름은 FDA 성분 사전(GSRS, openFDA substance)으로 맞춘다:
  이름 → UNII(성분 고유번호) → 염·용매화물이면 모체(PARENT) UNII.
  미국 이름(USAN)·국제 이름(INN)·일본 이름(JAN)이 한 UNII 에 모여 있어 표기 차이를 흡수한다.
  에스터·전구약물은 염이 아니므로 그대로 둔다(같은 활성 모체는 따로 표시).
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
from check_overseas import (FORM_EN, FORM_KO, en_moiety, form_of, get, head, korea)  # noqa: E402


def norm(n):
    n = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(n or "")).upper()        # 'AMLODIPINE BESILATE [JAN]' → 이름만
    n = re.sub(r"[^A-Z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


# ═══════════ FDA 성분 사전 ═══════════
def gsrs():
    head("FDA 성분 사전 (GSRS)")
    idx = json.loads(get("https://api.fda.gov/download.json", timeout=60))
    part = idx["results"]["other"]["substance"]["partitions"]
    t0 = time.time()
    import ijson
    name2u, parent, moiety, pname, rel_types, n = {}, {}, {}, {}, Counter(), 0
    for p in part:
        z = zipfile.ZipFile(io.BytesIO(get(p["file"], timeout=900)))
        for fn in z.namelist():
            with z.open(fn) as fp:
                for r in ijson.items(fp, "results.item"):
                    n += 1
                    u = r.get("unii")
                    if not u:
                        continue
                    for nm in r.get("names") or []:
                        k = norm(nm.get("name"))
                        if k:
                            name2u.setdefault(k, set()).add(u)
                    pn = next((x.get("name") for x in r.get("names") or [] if x.get("preferred")), None) \
                        or (r.get("names") or [{}])[0].get("name")
                    pname[u] = norm(pn)
                    for rel in r.get("relationships") or []:
                        ty = rel.get("type") or ""
                        rel_types[ty] += 1
                        ru = (rel.get("related_substance") or {}).get("unii")
                        if not ru:
                            continue
                        if ty == "SALT/SOLVATE->PARENT":
                            parent.setdefault(u, ru)
                        elif ty == "ACTIVE MOIETY":
                            moiety.setdefault(u, ru)
    print(f"성분 {n:,}개 · 이름 {len(name2u):,}개 · 염→모체 {len(parent):,} · 활성 모체 {len(moiety):,} · {time.time() - t0:.0f}초")
    print("   관계 종류 (많은 순):", rel_types.most_common(12))
    return name2u, parent, moiety, pname


def resolver(name2u, parent, moiety, pname):
    stats = Counter()

    def one(raw):
        """성분 이름 하나 → (비교 키, 활성 모체 이름, 어떻게 찾았나)"""
        cands = [norm(raw), norm(re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", str(raw)))]
        stripped = en_moiety(raw, syn=False)
        cands.append(norm(stripped))
        for i, c in enumerate(cands):
            us = name2u.get(c)
            if us and len(us) == 1:
                u = next(iter(us))
                pu = parent.get(u, u)
                how = ["이름 그대로", "괄호 뗌", "염 뗀 이름"][i]
                stats[how] += 1
                return "U:" + pu, pname.get(pu, ""), how
            if us:
                stats["이름이 여러 성분에 걸림"] += 1
        stats["사전에 없음"] += 1
        return "T:" + stripped, stripped.upper(), "사전에 없음"

    return one, stats


def main():
    kr = korea()
    head("미국 Drugs@FDA")
    idx = json.loads(get("https://api.fda.gov/download.json", timeout=60))
    apps = []
    for p in idx["results"]["drug"]["drugsfda"]["partitions"]:
        z = zipfile.ZipFile(io.BytesIO(get(p["file"], timeout=300)))
        for fn in z.namelist():
            apps += json.loads(z.read(fn))["results"]
    rows = []
    for a in apps:
        no = a.get("application_number", "")
        orig = [s.get("submission_status_date", "") for s in a.get("submissions") or []
                if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP"]
        for pr in a.get("products") or []:
            if pr.get("marketing_status") not in ("Prescription", "Over-the-counter"):
                continue
            rows.append({"app": no, "brand": pr.get("brand_name", ""), "ings": [x.get("name", "") for x in pr.get("active_ingredients") or []],
                         "fg": form_of(f"{pr.get('dosage_form', '')} {pr.get('route', '')}", FORM_EN),
                         "date": min(orig) if orig else "", "kind": "ANDA" if no.startswith("ANDA") else no[:3]})
    us = pd.DataFrame(rows)
    print(f"판매 중 제품 {len(us):,}개")

    name2u, parent, moiety, pname = gsrs()
    one, stats = resolver(name2u, parent, moiety, pname)
    cache = {}

    def res(x):
        if x not in cache:
            cache[x] = one(x)
        return cache[x]

    us["keys"] = us["ings"].map(lambda t: tuple(sorted({res(x)[0] for x in t if x.strip()})))
    print("미국 성분 이름 찾기:", dict(stats))
    stats.clear()

    st_c, gb_c, eng_c, name_c = "상태", "전문/일반", "주성분(영문)", "품목명"
    k = kr[kr[st_c].eq("정상") & kr[gb_c].astype(str).str.contains("전문|일반")].copy()
    split = lambda s: [x for x in re.split(r"/|;|\+|,(?=\s*[A-Za-z\[])", str(s)) if x.strip()]  # noqa: E731
    k["keys"] = k[eng_c].map(lambda s: tuple(sorted({res(x)[0] for x in split(s)})) if str(s).strip() else None)
    k["fg"] = k[name_c].map(lambda s: form_of(re.sub(
        r"\(.*?\)|\[.*?\]|\d+(\.\d+)?\s*(밀리그램|마이크로그램|그램|밀리리터|리터|mg|μg|㎍|㎎|g|mL|㎖|%|퍼센트|단위|IU|국제단위).*$",
        "", str(s)), FORM_KO))
    print("국내 성분 이름 찾기:", dict(stats))
    print("   국내에서 사전에 없던 이름 예:", [n for n, v in cache.items() if v[2] == "사전에 없음"][:40])
    print("   표기 차이가 흡수된 예:", [(n, v[1]) for n, v in cache.items()
                                if v[2] != "사전에 없음" and n.lower().split()[0] not in v[1].lower()][:30])

    label = {}
    for n, (key, mo, how) in cache.items():
        label.setdefault(key, mo or n)
    lab = lambda t: " + ".join(sorted(label.get(x, x) for x in t))  # noqa: E731

    us_set = {(t, f) for t, f in zip(us["keys"], us["fg"]) if t and isinstance(f, str)}
    kr_set = {(t, f) for t, f in zip(k["keys"], k["fg"]) if t and isinstance(f, str)}
    kr_keys = {t for t in k["keys"] if t}
    allk = kr.copy()
    allk["keys"] = allk[eng_c].map(lambda s: tuple(sorted({res(x)[0] for x in split(s)})) if str(s).strip() else None)
    past = Counter()
    for t, s in zip(allk["keys"], allk[st_c]):
        if t and s != "정상":
            past[t] += 1
    # 미국 신약(NDA·BLA)이 있는 성분만 (제네릭만 있는 옛 성분 포함 여부는 따로 셈)
    us_keys = {t for t, _ in us_set}
    gap = sorted(t for t in us_keys if t not in kr_keys)
    gap_single = [t for t in gap if len(t) == 1]
    gap_form = sorted((t, f) for t, f in us_set if t in kr_keys and (t, f) not in kr_set)
    unknown = [t for t in gap if any(x.startswith("T:") for x in t)]
    print(f"\n미국 (주성분+제형) {len(us_set):,} · 국내 {len(kr_set):,} · 미국 주성분 {len(us_keys):,} · 국내 주성분 {len(kr_keys):,}")
    print(f"▶ 미국에 있고 국내에 성분이 없음: {len(gap):,}가지 (단일제 {len(gap_single):,} · 국내에 취하·만료 품목은 있었음 {sum(1 for t in gap if past[t]):,}"
          f" · 이름을 사전에서 못 찾은 것 {len(unknown):,})")
    print("   단일제 전부:", [lab(t) + (" (국내 과거 허가)" if past[t] else "") for t in gap_single])
    print(f"▶ 국내에 성분은 있고 그 제형이 없음: {len(gap_form):,}가지 (단일제 {sum(1 for t, _ in gap_form if len(t) == 1):,})")
    print("   단일제 전부:", [f"{lab(t)} [{f}]" for t, f in gap_form if len(t) == 1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
