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


# ═══════════ FDA 성분 사전 → 공식 이름 동의어 표 ═══════════
OFFICIAL = re.compile(r"\[(INN|USAN|JAN|BAN|WHO-DD|USP|MI|EP|JP|DCF|MART\.|VANDF|HSDB)[^\]]*\]\s*$", re.I)


U2I = {}


def gsrs():
    """공식 이름(INN·USAN·JAN·USP 등 꼬리표가 붙은 이름)만 모아, 같은 성분(염은 모체로 모음)끼리 묶는다.
    반환: 이름(염 뗀 소문자) → {묶음}, 묶음 → 대표 이름(INN 우선)"""
    head("FDA 성분 사전 (GSRS) → 공식 이름 동의어 표")
    idx = json.loads(get("https://api.fda.gov/download.json", timeout=60))
    part = idx["results"]["other"]["substance"]["partitions"]
    t0 = time.time()
    import ijson
    syn, inn, usan, n = defaultdict(set), {}, {}, 0
    for p in part:
        z = zipfile.ZipFile(io.BytesIO(get(p["file"], timeout=900)))
        for fn in z.namelist():
            with z.open(fn) as fp:
                for r in ijson.items(fp, "results.item"):
                    n += 1
                    u = r.get("unii")
                    if not u:
                        continue
                    grp = next(((rel.get("related_substance") or {}).get("unii") for rel in r.get("relationships") or []
                                if rel.get("type") == "SALT/SOLVATE->PARENT"), None) or u
                    for nm in r.get("names") or []:
                        raw = nm.get("name") or ""
                        tag = OFFICIAL.search(raw)
                        if not tag:
                            continue
                        k = en_moiety(OFFICIAL.sub("", raw), syn=False)
                        if not k or len(k) > 60:
                            continue
                        syn[k].add(grp)
                        t = tag.group(1).upper()
                        if t == "INN":
                            inn.setdefault(grp, k)
                        elif t == "USAN":
                            usan.setdefault(grp, k)
    rep = {g: inn.get(g) or usan.get(g) for g in set(inn) | set(usan)}
    pairs = defaultdict(set)                     # 미국 이름(USAN) → 국제 이름(INN), FDA 가 한 성분에 함께 적어 둔 짝
    for g in set(inn) & set(usan):
        if inn[g] != usan[g]:
            pairs[usan[g]].add(inn[g])
    U2I.update({u: next(iter(i)) for u, i in pairs.items() if len(i) == 1})
    print(f"미국 이름 → 국제 이름 짝 {len(U2I):,}개 · 예 {list(U2I.items())[:12]}")
    print(f"성분 {n:,}개 · 공식 이름 {len(syn):,}개 · 대표 이름 있는 묶음 {len(rep):,} · {time.time() - t0:.0f}초")
    return syn, rep


NOISE_US = re.compile(r"technetium|tc 99m|\bkit\b|\bf 18\b|\bga 68\b|\bin 111\b|\bi 123\b|\bi 131\b|rubidium|thallous|"
                      r"\bnitrogen\b|\boxygen\b|\bhelium\b|nitrous oxide|carbon dioxide|\bxenon\b|\bkrypton\b|"
                      r"fludeoxyglucose|\bammonia n 13\b|allergenic|pollen|venom|\bmold\b|\bdander\b", re.I)



# ═══════════ 국내 임상시험 승인 목록 ═══════════
KO_SALT = sorted(["브롬화수소산염", "메탄술폰산염", "타르타르산염", "말레산염", "푸마르산염", "숙신산염", "베실산염", "메실산염",
                  "토실산염", "옥살산염", "살리실산염", "벤조산염", "브롬화물", "염화물", "요오드화물", "히드로클로라이드", "하이드로클로라이드", "글루콘산염", "구연산염", "시트르산염", "주석산염", "아세트산염", "초산염", "젖산염", "염산염",
                  "황산염", "인산염", "질산염", "탄산염", "이나트륨", "나트륨", "칼슘", "칼륨", "마그네슘", "아연",
                  "삼수화물", "이수화물", "일수화물", "반수화물", "수화물", "무수물", "염"], key=len, reverse=True)
_CHO = ["k", "k", "n", "t", "t", "r", "m", "p", "p", "s", "s", "", "j", "j", "j", "k", "t", "p", "h"]
_JONG = ["", "k", "k", "k", "n", "n", "n", "t", "r", "k", "m", "p", "r", "r", "p", "r", "m", "p", "p", "t", "t", "n",
         "t", "t", "k", "t", "p", "t"]


def ko_core(s):
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)|\s+", "", str(s or ""))
    changed = True
    while changed:
        changed = False
        for suf in KO_SALT:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s, changed = s[: -len(suf)], True
                break
    return s


def ko_skel(s):
    """한글 성분명 → 자음 뼈대 (영문 성분명과 비교용)"""
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        if not 0 <= c < 11172:
            return None
        out += [_CHO[c // 588], "a", _JONG[c % 28]]
    t = "".join(out).replace("h", "")
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiou]", "", t))


def en_skel_ko(e):
    s = re.sub(r"[^a-z]", "", e.lower())
    s = s.replace("ph", "p").replace("th", "t").replace("ch", "k").replace("qu", "k").replace("ck", "k").replace("x", "ks")
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k")
    for a, b in (("g", "k"), ("d", "t"), ("b", "p"), ("v", "p"), ("f", "p"), ("z", "j"), ("l", "r"), ("h", ""),
                 ("w", ""), ("y", "")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiou]", "", s))


def korea_trials(k, canon, split, wanted):
    """국내 임상시험 승인 목록 → {주성분 키: [(승인일, 단계, 의뢰사, 제품명, 제형)]} (wanted 성분만)"""
    head("국내 임상시험 승인 목록")
    sid = "1iFPo8V-JLFQEkFgkfAH2l8-FtUEia0on6Q8OjtPr6sE"
    raw = get(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv", timeout=300)
    if raw[:15].lstrip().lower().startswith(b"<!doctype") or raw[:6].lower() == b"<html":
        print("임상 시트가 공개 링크로 열리지 않음")
        return {}
    c = pd.read_csv(io.BytesIO(raw), dtype=str, low_memory=False).fillna("")
    c.columns = [str(x).split("\n")[0].strip() for x in c.columns]
    print(f"{len(c):,}행 · 칸 {list(c.columns)} · 상태 {Counter(c.get('STATUS', pd.Series(dtype=str))).most_common(5)}")
    print("   단계:", Counter(c.get("CLINIC_STEP_NM", pd.Series(dtype=str))).most_common(10))
    print("   승인일 범위:", c["CLST_APRV_DT"].min(), "~", c["CLST_APRV_DT"].max())
    print("   성분명 예:", list(c["성분명"].sample(25, random_state=2)))
    # 한글 성분 → 키: 허가 자료의 단일 성분 줄
    kor2key = {}
    for kor, eng in zip(k["주성분"], k["주성분(영문)"]):
        ks, es = [x for x in re.split(r"/", str(kor)) if x.strip()], split(eng)
        if len(ks) == 1 and len(es) == 1:
            kor2key.setdefault(ko_core(ks[0]), canon(es[0]))
    skel2key = defaultdict(set)
    for w in wanted:
        sk = en_skel_ko(w)
        if len(sk) >= 4:
            skel2key[sk].add(w)
    out, how = defaultdict(list), Counter()
    for nm, step, dt, sp, prod in zip(c["성분명"], c["CLINIC_STEP_NM"], c["CLST_APRV_DT"], c.get("원개발사", ""), c["제품명"]):
        parts = [p for p in re.split(r",|/|·|\+|및", str(nm)) if p.strip()]
        f = form_of(re.sub(r"\(.*?\)|\[.*?\]|\d.*$", "", str(prod)), FORM_KO)
        for p in parts:
            key = None
            lat = re.findall(r"[A-Za-z][A-Za-z\- ]{3,}", p)
            core = ko_core(p)
            if core in kor2key:
                key, h = kor2key[core], "허가 자료 한글명"
            elif lat and canon(lat[0]) in wanted:
                key, h = canon(lat[0]), "영문 표기"
            else:
                sk = ko_skel(core) if core else None
                hit = skel2key.get(sk) if sk else None
                if hit and len(hit) == 1:
                    key, h = next(iter(hit)), "한글 발음 대조"
                else:
                    h = None
            if key and key in wanted:
                out[key].append((dt, step, sp, prod, f))
                how[h] += 1
    print(f"후보 성분에 연결된 임상 {sum(len(v) for v in out.values()):,}건 · 성분 {len(out):,}개 · 연결 방법 {dict(how)}")
    print("   한글 발음 대조 예:", [(k_, v[0][3][:20]) for k_, v in list(out.items())[:0]])
    return out


def trial_txt(lst, f=None):
    if not lst:
        return ""
    lst = sorted(lst, reverse=True)
    dt, step, sp, prod, ff = lst[0]
    recent = sum(1 for x in lst if x[0][:4] >= "2021")
    same = sum(1 for x in lst if f and x[4] == f)
    return (f"국내 임상 {len(lst)}건(2021년 이후 {recent}) · 최근 {dt[:4]}-{dt[4:6]} {step} {sp[:20]}"
            + (f" · 이 제형 임상 {same}건" if f else ""))


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

    syn, rep = gsrs()
    stats = Counter()
    changed = {}

    def canon(x):
        k = en_moiety(x, syn=False)
        if k in U2I:
            changed[k] = U2I[k]
            stats["미국 이름 → 국제 이름"] += 1
            return U2I[k]
        g = syn.get(k)
        if g and len(g) == 1:
            c = rep.get(next(iter(g)))
            if c:
                stats["공식 이름으로 정리"] += 1
                if c != k:
                    changed[k] = c
                return c
            stats["공식 이름 있음(대표 없음)"] += 1
            return k
        if g:
            reps = {rep[x] for x in g if rep.get(x)}
            if k in reps:                       # 그 이름 자체가 어느 묶음의 대표 이름
                stats["여러 묶음 · 대표 이름과 같음"] += 1
                return k
            if len(reps) == 1:                  # 묶음은 여럿이지만 대표 이름은 하나 (라세미체·수화물 등)
                c = next(iter(reps))
                changed[k] = c
                stats["여러 묶음 · 대표 이름 하나"] += 1
                return c
            stats["여러 성분에 걸림"] += 1
            return k
        stats["공식 이름 아님"] += 1
        return k

    split_us = lambda t: [y for x in t for y in re.split(r"\s+AND\s+|;", x) if y.strip()]  # noqa: E731
    us = us[~us["ings"].map(lambda t: bool(NOISE_US.search(" ".join(t))))].copy()
    print(f"진단용 방사성·의료용 가스·알레르겐 뺀 미국 제품 {len(us):,}개")
    us["keys"] = us["ings"].map(lambda t: tuple(sorted({canon(x) for x in split_us(t)})))
    print("미국 성분 이름:", dict(stats))
    stats.clear()

    st_c, gb_c, eng_c, name_c = "상태", "전문/일반", "주성분(영문)", "품목명"
    k = kr[kr[st_c].eq("정상") & kr[gb_c].astype(str).str.contains("전문|일반")].copy()
    split = lambda s: [x for x in re.split(r"/|;|\+|,(?=\s*[A-Za-z\[])", str(s)) if x.strip()]  # noqa: E731
    k["keys"] = k[eng_c].map(lambda s: tuple(sorted({canon(x) for x in split(s)})) if str(s).strip() else None)
    k["fg"] = k[name_c].map(lambda s: form_of(re.sub(
        r"\(.*?\)|\[.*?\]|\d+(\.\d+)?\s*(밀리그램|마이크로그램|그램|밀리리터|리터|mg|μg|㎍|㎎|g|mL|㎖|%|퍼센트|단위|IU|국제단위).*$",
        "", str(s)), FORM_KO))
    print("국내 성분 이름:", dict(stats))
    for probe in ("salbutamol", "albuterol", "cefalexin", "cephalexin", "nilotinib", "betamethasone", "miconazole",
                  "sevoflurane", "nitroglycerin", "glyceryl", "magnesium sulfate", "efavirenz", "citalopram", "terbutaline"):
        h = kr[kr[eng_c].astype(str).str.lower().str.contains(probe)]
        ex = [(str(e)[:70], st, gb[:2], tuple(sorted({canon(x) for x in split(e)}))) for e, st, gb in
              zip(h[eng_c], h[st_c], h[gb_c])][:4]
        print(f"   [국내 표기] {probe}: {len(h)}품목 · 정상 {int((h[st_c] == '정상').sum())} · {ex}")
    for nm in ("ALBUTEROL SULFATE", "CEPHALEXIN", "NILOTINIB HYDROCHLORIDE", "BETAMETHASONE VALERATE", "MICONAZOLE NITRATE",
               "SEVOFLURANE", "NITROGLYCERIN", "EFAVIRENZ", "CITALOPRAM HYDROBROMIDE"):
        k0 = en_moiety(nm, syn=False)
        print(f"   [미국 표기] {nm} → {k0} → 묶음 {len(syn.get(k0, ()))}개 · 대표 {[rep.get(g) for g in list(syn.get(k0, ()))[:4]]} → {canon(nm)}")
    print("   표기 차이가 공식 이름으로 정리된 예:", list(changed.items())[:40])

    lab = lambda t: " + ".join(t)  # noqa: E731

    us_set = {(t, f) for t, f in zip(us["keys"], us["fg"]) if t and isinstance(f, str)}
    kr_set = {(t, f) for t, f in zip(k["keys"], k["fg"]) if t and isinstance(f, str)}
    kr_keys = {t for t in k["keys"] if t}
    allk = kr.copy()
    allk["keys"] = allk[eng_c].map(lambda s: tuple(sorted({canon(x) for x in split(s)})) if str(s).strip() else None)
    past = Counter()
    for t, s in zip(allk["keys"], allk[st_c]):
        if t and s != "정상":
            past[t] += 1
    # 미국 신약(NDA·BLA)이 있는 성분만 (제네릭만 있는 옛 성분 포함 여부는 따로 셈)
    us_keys = {t for t, _ in us_set}
    gap = sorted(t for t in us_keys if t not in kr_keys)
    gap_single = [t for t in gap if len(t) == 1]
    gap_form = sorted((t, f) for t, f in us_set if t in kr_keys and (t, f) not in kr_set)
    unknown = [t for t in gap if any(syn.get(x) is None for x in t)]
    print(f"\n미국 (주성분+제형) {len(us_set):,} · 국내 {len(kr_set):,} · 미국 주성분 {len(us_keys):,} · 국내 주성분 {len(kr_keys):,}")
    print(f"▶ 미국에 있고 국내에 성분이 없음: {len(gap):,}가지 (단일제 {len(gap_single):,} · 국내에 취하·만료 품목은 있었음 {sum(1 for t in gap if past[t]):,}"
          f" · 이름을 사전에서 못 찾은 것 {len(unknown):,})")
    wanted = {x for t in gap for x in t} | {x for t, _ in gap_form for x in t}
    try:
        trials = korea_trials(k, canon, split, wanted)
    except Exception as e:
        print("임상 목록 실패:", type(e).__name__, str(e)[:200])
        trials = {}
    ttx = lambda t, f=None: " ; ".join(filter(None, (trial_txt(trials.get(x, []), f) and f"{x}: {trial_txt(trials.get(x, []), f)}" if len(t) > 1 else trial_txt(trials.get(x, []), f) for x in t)))  # noqa: E731
    print(f"   국내 성분 없음 후보 중 국내 임상 있음: {sum(1 for t in gap if any(trials.get(x) for x in t))}가지"
          f" · 제형 없음 후보 중 같은 성분 국내 임상 있음: {sum(1 for t, _ in gap_form if any(trials.get(x) for x in t))}가지")
    info = defaultdict(lambda: {"date": "9", "brands": Counter(), "anda": set(), "nda": set(), "forms": set()})
    for t, f, br, d, kd, app in zip(us["keys"], us["fg"], us["brand"], us["date"], us["kind"], us["app"]):
        x = info[t]
        if kd != "ANDA" and d and d < x["date"]:
            x["date"] = d
        if kd != "ANDA":
            x["brands"][br] += 1
            x["nda"].add(app)
        else:
            x["anda"].add(app)
        if isinstance(f, str):
            x["forms"].add(f)
    print("   [목록] 구분 | 주성분 | 미국 첫 허가 | 상표 | 오리지널 허가 수 | 제네릭 허가 수 | 제형 | 비고")
    for t in gap:
        x = info[t]
        print(f"   [목록] 국내 성분 없음 | {lab(t)} | {x['date'][:4] if x['date'] != '9' else ''} | "
              f"{', '.join(b for b, _ in x['brands'].most_common(2))} | {len(x['nda'])} | {len(x['anda'])} | {', '.join(sorted(x['forms']))} | "
              f"{'국내 과거 허가' if past[t] else ''}{' · 공식 이름 아님' if t in unknown else ''} | {ttx(t)}")
    print(f"▶ 국내에 성분은 있고 그 제형이 없음: {len(gap_form):,}가지 (단일제 {sum(1 for t, _ in gap_form if len(t) == 1):,})")
    kforms = defaultdict(set)
    for t, f in kr_set:
        kforms[t].add(f)
    for t, f in gap_form:
        x = info[t]
        print(f"   [목록] 국내 이 제형 없음 | {lab(t)} | {x['date'][:4] if x['date'] != '9' else ''} | "
              f"{', '.join(b for b, _ in x['brands'].most_common(2))} | {len(x['nda'])} | {len(x['anda'])} | {f} | 국내 제형: {', '.join(sorted(kforms[t]))} | {ttx(t, f)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
