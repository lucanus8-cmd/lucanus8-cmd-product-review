"""일본(PMDA·후생노동성) 자료를 사이트 'FDA 비교'처럼 붙일 수 있는지 재 본다. 사이트와 상관없다(읽기만).

A. 일본 공식 일반명 DB(JAN, 국립의약품식품위생연구소) — 일본어명 ↔ 영문명 전체 목록을 받을 수 있는지
B. 후생노동성 약가 목록(일본 보험 전문약 전부)의 성분명을 A 로 영문으로 몇 % 바꾸는지
C. 바꾼 영문 성분이 미국(FDA 비교 표)·국내와 몇 개 겹치는지 (위키데이터 안 씀)
D. PMDA 영문 신약 허가 목록 — 표 구조·연도 범위·적응증 칸이 있는지
E. PMDA 첨부문서(적응증 원문) 일괄 내려받기가 있는지 — 화면 링크만 본다
"""
import io
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter

import pandas as pd

sys.path.insert(0, ".github/scripts")
import check_overseas as ov          # noqa: E402  (약가 목록 읽기·염 떼기 재사용)

UA = {"User-Agent": "Mozilla/5.0 (product-review feasibility check)"}


def get(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except Exception:
            if i == 2:
                raise
            time.sleep(5 * (i + 1))


def dec(b, ct=""):
    m = re.search(r"charset=([\w\-]+)", ct or "") or re.search(rb'charset=["\']?([\w\-]+)', b[:3000])
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return b.decode(enc, "ignore")
    except LookupError:
        return b.decode("utf-8", "ignore")


def head(t):
    print("\n" + "=" * 8, t, flush=True)


def cells(tr):
    return [re.sub(r"\s+", " ", unicodedata.normalize("NFKC", re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]


KANA = re.compile(r"[ァ-ヴー]")
LATIN = re.compile(r"^[A-Za-z][A-Za-z0-9 ,'\-\(\)\.\[\]/]+$")


# ═══════════ A. JAN 전체 목록 ═══════════
def jan():
    head("A. 일본 공식 일반명 DB (JAN) 전체 목록")
    base = "https://jpdb.nihs.go.jp/jan/SimpleList_ja?submit=all_alp&display=jan_name_e_disp&keyword="
    rows, first = [], True
    for L in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        try:
            b, ct = get(base + L)
        except Exception as e:
            print(f"   {L}: 못 받음 {type(e).__name__} {str(e)[:80]}")
            continue
        t = dec(b, ct)
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S | re.I)
        got = []
        for tr in trs:
            c = cells(tr)
            ja = next((x for x in c if KANA.search(x) or re.search(r"[一-龥]", x)), "")
            en = next((x for x in c if LATIN.match(x) and len(x) > 2), "")
            if ja and en:
                got.append((ja, en))
        if first:
            print(f"   A 화면 {len(t):,}자 · 줄 {len(trs)} · 짝 {len(got)}")
            for tr in trs[:4]:
                print("     줄 예:", cells(tr)[:6])
            pg = sorted(set(re.findall(r'href="([^"]*(?:page|Page|start|offset)[^"]*)"', t)))[:8]
            print("     쪽 넘김 링크 예:", pg)
            first = False
        rows += got
        time.sleep(1.0)
    df = pd.DataFrame(rows, columns=["ja", "en"]).drop_duplicates()
    print(f"JAN 짝 {len(df):,}개 · 일본어명 {df['ja'].nunique():,} · 예 {df.sample(min(6, len(df)), random_state=1).values.tolist() if len(df) else []}")
    return df


# ═══════════ B·C. 약가 목록 → 영문 ═══════════
def ja_norm(s):
    return re.sub(r"[\s　・･]", "", unicodedata.normalize("NFKC", str(s or "")))


def coverage(jp, jn):
    head("B. 약가 목록 성분명 → 영문 (JAN 만 사용)")
    if jp is None or jn is None or jn.empty:
        print("자료 없음")
        return {}
    full = {ja_norm(a): b for a, b in zip(jn["ja"], jn["en"])}
    moi = {}
    for a, b in zip(jn["ja"], jn["en"]):
        moi.setdefault(ov.ja_moiety(ja_norm(a)), set()).add(ov.en_moiety(b))
    name_col = next(c for c in jp.columns if "成分名" in c)
    parts = Counter()
    hit_full = hit_moi = 0
    out = {}
    for s in jp[name_col].astype(str):
        for p in re.split(r"[・／/、,]", s):
            p = p.strip()
            if p:
                parts[p] += 1
    miss = Counter()
    for p, n in parts.items():
        k = ja_norm(p)
        if k in full:
            out[p] = ov.en_moiety(full[k])
            hit_full += n
        else:
            m = ov.ja_moiety(k)
            c = moi.get(m)
            if c and len(c) == 1:
                out[p] = next(iter(c))
                hit_moi += n
            else:
                miss[p] += n
    tot = sum(parts.values())
    print(f"약가 목록 성분 표기 {len(parts):,}가지 (제품×성분 {tot:,})")
    print(f"   그대로 맞음 {hit_full / tot * 100:.1f}% · 염 떼고 맞음 {hit_moi / tot * 100:.1f}% · "
          f"합계 {(hit_full + hit_moi) / tot * 100:.1f}% (제품×성분 기준)")
    print(f"   성분 표기 기준 {len(out) / len(parts) * 100:.1f}%")
    print("   못 바꾼 것 많은 순:", miss.most_common(40))
    # 제품 기준: 성분이 모두 바뀐 제품 비율
    allok = jp[name_col].astype(str).map(lambda s: all(p.strip() in out for p in re.split(r"[・／/、,]", s) if p.strip()))
    print(f"   제품 기준(성분 모두 영문) {allok.mean() * 100:.1f}% · {allok.sum():,}/{len(jp):,}")
    return out


def overlap(jp, out):
    head("C. 일본 성분(영문) ↔ 미국 FDA 비교 표 겹침")
    if not out:
        return
    try:
        b, _ = get("https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/fda_site/ing.parquet")
        ing = pd.read_parquet(io.BytesIO(b))
    except Exception as e:
        print("FDA 표 못 읽음:", str(e)[:100])
        return
    us = set(ing["주성분"].str.lower())
    us_single = {x for x in us if "+" not in x}
    jp_moi = {v for v in out.values() if v}
    both = jp_moi & us_single
    print(f"일본 단일 성분(영문) {len(jp_moi):,} · 미국 단일 성분 {len(us_single):,} · 겹침 {len(both):,}")
    print("   일본에만 있는 예:", sorted(jp_moi - us_single)[:60])
    kr_gap = set(ing.loc[ing["국내 상황"] == "국내 성분 없음", "주성분"].str.lower())
    print(f"   미국·일본 둘 다 있고 국내 성분 없음(미국 표 기준): {len(both & kr_gap)}개 · 예 {sorted(both & kr_gap)[:40]}")


# ═══════════ D. PMDA 영문 신약 목록 ═══════════
def pmda_list():
    head("D. PMDA 영문 신약 허가 목록")
    url = "https://www.pmda.go.jp/english/review-services/reviews/approved-information/drugs/0001.html"
    b, ct = get(url)
    t = dec(b, ct)
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S | re.I)
    rows = [cells(tr) for tr in trs]
    rows = [r for r in rows if len(r) >= 3]
    print(f"화면 {len(t):,}자 · 표 줄 {len(rows)}")
    for r in rows[:5]:
        print("   줄 예:", [x[:60] for x in r[:8]])
    yrs = Counter(re.findall(r"\b(20[0-2]\d)\b", " ".join(" ".join(r) for r in rows)))
    print("   연도 분포:", sorted(yrs.items()))
    subs = sorted(set(re.findall(r'href="([^"]*approved-information/drugs/\d{4}\.html)"', t)))
    print("   다른 쪽:", subs[:20])
    xl = sorted(set(re.findall(r'href="([^"]+\.(?:xlsx|xls|csv|pdf))"', t, re.I)))
    print(f"   파일 링크 {len(xl)}개 · 예 {xl[:6]}")
    # 신약 목록(List of Approved Products) 엑셀·PDF 가 있는지
    for u in xl[:3]:
        if re.search(r"xlsx?|csv", u, re.I):
            try:
                bb, _ = get(urllib.parse.urljoin(url, u))
                x = pd.read_excel(io.BytesIO(bb), header=None, dtype=str)
                print(f"   {u.split('/')[-1]}: {x.shape} · 앞줄 {x.head(4).values.tolist()}")
            except Exception as e:
                print("   엑셀 못 읽음:", str(e)[:80])


# ═══════════ E. 첨부문서 일괄 ═══════════
def pmda_labels():
    head("E. PMDA 첨부문서(적응증 원문) 일괄 받기 단서")
    for url in ("https://www.pmda.go.jp/safety/info-services/drugs/0001.html",
                "https://www.pmda.go.jp/PmdaSearch/iyakuSearch/",
                "https://www.pmda.go.jp/english/safety/info-services/drugs/0001.html"):
        try:
            b, ct = get(url)
            t = dec(b, ct)
            ln = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', t, re.S)
            hits = [(h, re.sub(r"<[^>]+>|\s+", " ", x).strip()[:50]) for h, x in ln
                    if re.search(r"一括|ダウンロード|download|XML|SGML|bulk", h + x, re.I)]
            print(f"{url.split('.jp')[1]} · {len(t):,}자 · 관련 링크 {hits[:12]}")
        except Exception as e:
            print(url, "못 받음", str(e)[:80])


def probe():
    """JAN 화면 구조 보기: 첫 화면 링크 전부 · A 목록 줄 HTML · display 값 바꿔 보기 · 상세 화면 하나."""
    head("JAN 구조 보기")
    b, ct = get("https://jpdb.nihs.go.jp/jan/")
    t = dec(b, ct)
    ln = sorted(set(re.findall(r'href="([^"]+)"', t)))
    print("첫 화면 링크(일부 생략):", [x for x in ln if "keyword=" not in x or x.endswith(("=A", "=ア", "=あ"))][:60])
    print("   목록 링크 모양:", sorted({re.sub(r"keyword=[^&]*", "keyword=…", x) for x in ln if "List" in x}))
    b, ct = get("https://jpdb.nihs.go.jp/jan/SimpleList_ja?submit=all_alp&display=jan_name_e_disp&keyword=A")
    t = dec(b, ct)
    trs = re.findall(r"<tr[^>]*>.*?</tr>", t, re.S | re.I)
    for tr in trs[:3]:
        print("   A 줄 HTML:", re.sub(r"\s+", " ", tr)[:400])
    det = re.findall(r'href="([^"]*Detail[^"]*)"', t)
    print("   상세 링크 예:", det[:3], "· 개수", len(det))
    sel = re.findall(r"<select.*?</select>|<input[^>]+radio[^>]+>", t, re.S | re.I)
    print("   선택 칸:", [re.sub(r"\s+", " ", x)[:300] for x in sel[:6]])
    for disp in ("jan_name_j_disp", "jan_name_disp", "both", "all"):
        for sub in ("all_alp", "all_kana"):
            for kw in ("A", "ア"):
                u = (f"https://jpdb.nihs.go.jp/jan/SimpleList_ja?submit={sub}&display={disp}&keyword="
                     + urllib.parse.quote(kw))
                try:
                    bb, cc = get(u, timeout=40)
                    tt = dec(bb, cc)
                    rr = [cells(x) for x in re.findall(r"<tr[^>]*>(.*?)</tr>", tt, re.S | re.I)]
                    print(f"   {sub} {disp} {kw}: 줄 {len(rr)} · 예 {rr[1:3]}")
                except Exception as e:
                    print(f"   {sub} {disp} {kw}: {str(e)[:60]}")
                time.sleep(0.5)
    if det:
        u = urllib.parse.urljoin("https://jpdb.nihs.go.jp/jan/", det[0].replace("&amp;", "&"))
        bb, cc = get(u)
        tt = dec(bb, cc)
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tt))
        print("   상세 화면:", u, "·", txt[:900])


def main():
    import os
    if os.environ.get("PROBE") == "1":
        probe()
        return 0
    jn = jp = None
    try:
        jn = jan()
    except Exception as e:
        print("[A] 실패", type(e).__name__, str(e)[:200])
    try:
        jp = ov.japan()
    except Exception as e:
        print("[약가] 실패", type(e).__name__, str(e)[:200])
    out = {}
    try:
        out = coverage(jp, jn)
    except Exception as e:
        import traceback
        traceback.print_exc()
    try:
        overlap(jp, out)
    except Exception as e:
        print("[C] 실패", str(e)[:200])
    for fn in (pmda_list, pmda_labels):
        try:
            fn()
        except Exception as e:
            print(f"[{fn.__name__}] 실패", type(e).__name__, str(e)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
