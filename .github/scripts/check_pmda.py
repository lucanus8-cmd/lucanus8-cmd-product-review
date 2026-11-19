"""일본(PMDA·후생노동성) 자료를 사이트 'FDA 비교'처럼 붙일 수 있는지 재 본다. 사이트와 상관없다(읽기만).

A. 일본 공식 일반명 DB(JAN, 국립의약품식품위생연구소) — 일본어명 ↔ 영문명 전체 목록을 받을 수 있는지
B. 후생노동성 약가 목록(일본 보험 전문약 전부)의 성분명을 A 로 영문으로 몇 % 바꾸는지
C. 바꾼 영문 성분이 미국(FDA 비교 표)·국내와 몇 개 겹치는지 (위키데이터 안 씀)
D. PMDA 영문 신약 허가 목록 — 표 구조·연도 범위·적응증 칸이 있는지
E. PMDA 첨부문서(적응증 원문) 일괄 내려받기가 있는지 — 화면 링크만 본다
"""
import html
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
    out = []
    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I):
        c = unicodedata.normalize("NFKC", html.unescape(re.sub(r"<[^>]+>", " ", c)))
        c = re.sub(r"日局収載|日本薬局方収載", " ", c)        # 일본약국방 수재 표시
        out.append(re.sub(r"\s+", " ", c).strip())
    return out


KANA = re.compile(r"[ァ-ヴー]")
LATIN = re.compile(r"^[A-Za-z][A-Za-z0-9 ,'\-\(\)\.\[\]/]+$")


# ═══════════ A. JAN 전체 목록 ═══════════
def _jan_list(L, disp):
    u = f"https://jpdb.nihs.go.jp/jan/SimpleList_ja?submit=all_alp&display={disp}&keyword={L}"
    b, ct = get(u)
    t = dec(b, ct)
    return [" ".join(c) for c in (cells(x) for x in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S | re.I))]


def jan():
    """영문 보기와 일본어 보기가 같은 순서·같은 줄 수 → 나란히 붙이고, 상세 화면으로 표본 검증."""
    head("A. 일본 공식 일반명 DB (JAN) 전체 목록")
    try:
        b, ct = get("https://jpdb.nihs.go.jp/jan/list/index.html")
        t = dec(b, ct)
        print("   /jan/list/ 화면:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[:300],
              "· 파일 링크", re.findall(r'href="([^"]+\.(?:xlsx?|csv|pdf|zip|txt))"', t, re.I)[:10])
    except Exception as e:
        print("   /jan/list/ 못 받음", str(e)[:80])
    rows, bad = [], []
    for L in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        try:
            e = _jan_list(L, "jan_name_e_disp")
            time.sleep(0.7)
            j = _jan_list(L, "jan_name_j_disp")
            time.sleep(0.7)
        except Exception as ex:
            print(f"   {L}: 못 받음 {str(ex)[:80]}")
            continue
        if len(e) != len(j):
            bad.append((L, len(e), len(j)))
            continue
        rows += [(jj, ee, L) for ee, jj in zip(e, j) if ee and jj]
    df = pd.DataFrame(rows, columns=["ja", "en", "L"])
    print(f"JAN 짝 {len(df):,}개 · 줄 수 안 맞은 글자 {bad}")
    # 표본 검증: 상세 화면의 일본어명이 짝과 같은지
    smp = df.sample(min(40, len(df)), random_state=7)
    ok = 0
    for ja, en, _ in smp.values:
        try:
            u = ("https://jpdb.nihs.go.jp/jan/DetailList_ja?submit=all_alp%E6%A4%9C%E7%B4%A2&keyword="
                 + urllib.parse.quote(en))
            bb, cc = get(u, timeout=40)
            tx = re.sub(r"\s+", "", unicodedata.normalize("NFKC", html.unescape(re.sub(r"<[^>]+>", " ", dec(bb, cc)))))
            good = re.sub(r"\s+", "", ja) in tx
            ok += good
            if not good:
                print("     불일치:", ja, "|", en, "|", tx[tx.find("検索件数"):tx.find("検索件数") + 80])
        except Exception:
            pass
        time.sleep(0.5)
    print(f"   표본 {len(smp)}개 중 상세 화면과 일치 {ok}개")
    print("   예:", df.sample(min(8, len(df)), random_state=1)[["ja", "en"]].values.tolist())
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


def probe_label():
    """첨부문서(電子添文)를 약가 코드(YJ)·성분명으로 받을 수 있는 주소 찾기. 몇 개만 요청한다."""
    head("첨부문서 받는 주소 찾기")
    jp = ov.japan()
    code_c = next(c for c in jp.columns if "コード" in c)
    name_c = next(c for c in jp.columns if "成分名" in c)
    item_c = next(c for c in jp.columns if "品名" in c)
    sen_c = next((c for c in jp.columns if c.strip() == "先発医薬品"), None)
    for nm in ("アムロジピンベシル酸塩", "アトルバスタチンカルシウム水和物", "オランザピン"):
        x = jp[jp[name_c].astype(str).str.strip() == nm]
        if sen_c:
            x = x.sort_values(sen_c, ascending=False, key=lambda s: s.astype(str).str.len())
        if x.empty:
            print(nm, "약가 목록에 없음")
            continue
        yj, item = str(x.iloc[0][code_c]).strip(), str(x.iloc[0][item_c]).strip()
        print(f"\n▶ {nm} · YJ {yj} · {item} · 先発 {x.iloc[0][sen_c] if sen_c else ''}")
        tries = [f"https://www.info.pmda.go.jp/go/pack/{yj}/",
                 f"https://www.info.pmda.go.jp/go/pack/{yj}_1_01/",
                 f"https://www.pmda.go.jp/PmdaSearch/iyakuDetail/GeneralList/{yj[:7]}",
                 f"https://www.pmda.go.jp/PmdaSearch/iyakuDetail/GeneralList/{yj[:9]}",
                 f"https://www.pmda.go.jp/PmdaSearch/rdSearch/02/{yj}?user=1"]
        for u in tries:
            try:
                req = urllib.request.Request(u, headers=UA)
                with urllib.request.urlopen(req, timeout=40) as r:
                    b, fu, ct = r.read(), r.geturl(), r.headers.get("Content-Type", "")
                t = dec(b, ct)
                links = sorted(set(re.findall(r'href="([^"]*(?:ResultDataSet|iyakuDetail|pack|PDF|XML)[^"]*)"', t, re.I)))
                i = t.find("効能")
                print(f"   {u} → {fu[:110]} · {len(t):,}자 · 効能又は効果 {'効能又は効果' in t or '効能・効果' in t}"
                      f" · 링크 {links[:6]}")
                if i >= 0:
                    print("      글:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[i:i + 1500]))[:400])
            except Exception as e:
                print(f"   {u} → {type(e).__name__} {str(e)[:70]}")
            time.sleep(1.5)
    # 검색 화면(POST) — 이름으로
    try:
        data = urllib.parse.urlencode({"nameWord": "アムロジピン", "iyakuHowtoNameSearchRadioValue": "1",
                                       "howtoMatchRadioValue": "1", "ListRows": "10", "btnA.x": "1",
                                       "dispColumnsList[0]": "1", "dispColumnsList[1]": "2"}).encode()
        req = urllib.request.Request("https://www.pmda.go.jp/PmdaSearch/iyakuSearch/", data=data, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            t = dec(r.read(), r.headers.get("Content-Type", ""))
        links = sorted(set(re.findall(r'href="([^"]*(?:ResultDataSet|iyakuDetail|GeneralList)[^"]*)"', t)))
        print(f"\n검색(POST) · {len(t):,}자 · 결과 링크 {len(links)} · 예 {links[:10]}")
        print("   글:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[2000:2800])
    except Exception as e:
        print("검색(POST) 실패", str(e)[:100])


def probe_update():
    """매일 '바뀐 첨부문서만' 받을 길 찾기: 검색 화면의 갱신일 칸 · PMDA 새 소식(RSS) 주소. 몇 개만 요청한다."""
    head("갱신일로 찾기 · 새 소식 주소 찾기")
    for u in ("https://www.pmda.go.jp/PmdaSearch/iyakuSearch/", "https://www.pmda.go.jp/",
              "https://www.pmda.go.jp/rss/", "https://www.pmda.go.jp/safety/info-services/drugs/0001.html"):
        try:
            b, ct = get(u, timeout=60)
            t = dec(b, ct)
        except Exception as e:
            print(u, "실패", str(e)[:80])
            continue
        print(f"\n▶ {u} · {len(t):,}자")
        rss = sorted(set(re.findall(r'href="([^"]*(?:rss|RSS|\.xml|atom)[^"]*)"', t)))
        print("   새 소식(RSS) 링크:", rss[:20])
        if "iyakuSearch" in u:
            for m in re.finditer(r"<(input|select)[^>]*name=\"([^\"]+)\"[^>]*>", t):
                tag, nm = m.group(1), m.group(2)
                around = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[max(0, m.start() - 300):m.start()]))[-60:]
                if re.search(r"date|Date|day|Day|year|Year|update|Update|kaitei|Kaitei|kousin|koushin|from|to", nm) \
                        or re.search(r"更新|改訂|日付|期間|年月日", around):
                    opts = re.findall(r"<option[^>]*value=\"([^\"]*)\"[^>]*>([^<]*)", t[m.start():m.start() + 3000])[:8] \
                        if tag == "select" else []
                    print(f"   칸 {tag} {nm} · 앞 글 …{around.strip()} · 고르기 {opts}")
            for k in ("更新日", "改訂", "新着", "最新", "期間"):
                i = t.find(k)
                if i >= 0:
                    print(f"   '{k}' 주변:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[i - 200:i + 400]))[:300])
            forms = re.findall(r"<form[^>]*>", t)
            print("   form:", forms[:4])
        time.sleep(1.5)
    for u in ("https://www.pmda.go.jp/rss_008.xml", "https://www.pmda.go.jp/rss_015.xml", "https://www.pmda.go.jp/rss_001.xml"):
        try:
            b, ct = get(u, timeout=40)
            t = dec(b, ct)
            items = re.findall(r"<title>(.*?)</title>", t, re.S)
            print(f"\n{u} · {len(t):,}자 · 제목 {len(items)}개 · 예 {[html.unescape(x)[:60] for x in items[:6]]}")
        except Exception as e:
            print(u, "실패", str(e)[:60])
        time.sleep(1.5)


def probe_update2():
    """검색 화면 '更新年月日で検索'(updateDocFrDt~updateDocToDt)으로 최근 바뀐 첨부문서 목록이 나오는지. 몇 번만 요청."""
    head("갱신일로 검색 시험")
    import datetime as dt
    b, ct = get("https://www.pmda.go.jp/PmdaSearch/iyakuSearch/", timeout=60)
    t = dec(b, ct)
    # 화면의 기본값 그대로 (숨은 칸 포함) 가져온 뒤 날짜만 바꾼다
    base = {}
    for m in re.finditer(r"<input([^>]*)>", t):
        a = m.group(1)
        nm = re.search(r'name="([^"]+)"', a)
        if not nm:
            continue
        ty = (re.search(r'type="([^"]+)"', a) or [None, "text"])[1]
        val = (re.search(r'value="([^"]*)"', a) or [None, ""])[1]
        if ty in ("radio", "checkbox") and "checked" not in a:
            continue
        if ty in ("submit", "button", "image"):
            continue
        base.setdefault(nm.group(1), val)
    for m in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', t, re.S):
        sel = re.search(r'<option[^>]*selected[^>]*value="([^"]*)"|<option[^>]*value="([^"]*)"[^>]*selected', m.group(2))
        first = re.search(r'<option[^>]*value="([^"]*)"', m.group(2))
        base.setdefault(m.group(1), (sel.group(1) or sel.group(2)) if sel else (first.group(1) if first else ""))
    print("기본 칸 수", len(base), "· 날짜 칸 기본값", {k: base.get(k) for k in ("updateDocFrDt", "updateDocToDt", "ListRows")})
    today = dt.date(2026, 11, 19)
    for fr, to, fmt in ((today - dt.timedelta(days=1), today, "%Y%m%d"),):
        d = dict(base)
        d.update({"updateDocFrDt": fr.strftime(fmt), "updateDocToDt": to.strftime(fmt), "ListRows": "100"})
        for k in ("btnA.x", "btnA.y"):
            d[k] = "1"
        try:
            req = urllib.request.Request("https://www.pmda.go.jp/PmdaSearch/iyakuSearch/",
                                         data=urllib.parse.urlencode(d).encode(), headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                tt = dec(r.read(), r.headers.get("Content-Type", ""))
        except Exception as e:
            print(fr, to, fmt, "실패", str(e)[:80])
            continue
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tt))
        n = re.search(r"(\d[\d,]*)\s*件", txt)
        links = re.findall(r'href="([^"]*iyakuDetail/(?:ResultDataSetPDF|GeneralList|[^"/]*_\d+_\d+)[^"]*)"', tt)
        err = re.findall(r"(?:エラー|入力してください|正しく|指定してください)[^。]{0,60}", txt)[:3]
        print(f"\n{fr}~{to} ({fmt}) · {len(tt):,}자 · 건수 {n.group(0) if n else '?'} · 문서 링크 {len(links)}개 · 오류글 {err}")
        print("   링크 예:", sorted(set(links))[:8])
        for pat in (r"\b\d{4}[0-9A-Z]{3}[A-Z]\d{4}\b", r"lnkStrHTML\s*=", r"lnkStrPDF\s*=", r"var\s+\w*(?:result|Result|data|Data)\w*\s*=",
                    r"ResultDataSet", r"iyakuDetail"):
            ms = list(re.finditer(pat, tt))
            print(f"   [{pat}] {len(ms)}개")
            for mm in ms[:2]:
                print("      …", re.sub(r"\s+", " ", tt[max(0, mm.start() - 250):mm.start() + 450]))
        try:
            import http.cookiejar
            cj = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            op.addheaders = list(UA.items())
            op.open("https://www.pmda.go.jp/PmdaSearch/iyakuSearch/", timeout=60).read()
            op.open(urllib.request.Request("https://www.pmda.go.jp/PmdaSearch/iyakuSearch/",
                                           data=urllib.parse.urlencode(d).encode()), timeout=90).read()
            with op.open(urllib.request.Request("https://www.pmda.go.jp/PmdaSearch/iyakuSearch/exportSearchResult/csv",
                                                data=urllib.parse.urlencode({"searchNameTitle": ""}).encode()), timeout=90) as r:
                cb, cct = r.read(), r.headers.get("Content-Type", "")
            ctext = dec(cb, cct) if "utf" in cct.lower() else cb.decode("cp932", "ignore")
            print(f"   CSV 내보내기 · {cct} · {len(cb):,}바이트 · 줄 {ctext.count(chr(10))}")
            print("      ", ctext[:1500])
        except Exception as e:
            print("   CSV 내보내기 실패", type(e).__name__, str(e)[:120])
        hr = sorted(set(re.findall(r'href="([^"]+)"', tt)))
        print("   모든 링크(검색 관련):", [h for h in hr if "Pmda" in h or "pmda" in h or "/" not in h[:1]][:40])
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tt, re.S | re.I)
        rows = [cells(x) for x in trs]
        rows = [r for r in rows if any(r)]
        print(f"   표 줄 {len(rows)}개")
        for r_, raw in list(zip(rows, [x for x in trs if any(cells(x))]))[:6]:
            print("     ", r_[:10])
            print("        HTML:", re.sub(r"\s+", " ", raw)[:700])
        time.sleep(3)


def main():
    import os
    if os.environ.get("PROBE") == "update2":
        probe_update2()
        return 0
    if os.environ.get("PROBE") == "update":
        probe_update()
        return 0
    if os.environ.get("PROBE") in ("1", "probe"):
        probe()
        return 0
    if os.environ.get("PROBE") == "label":
        probe_label()
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
