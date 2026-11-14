"""식약처 의약품안전나라의 '원료의약품등록(DMF) 공고' 목록을 전부 받아 CSV 로 저장한다.

공개 자료만 받는다. 매출 자료는 읽지도 적지도 않는다.
화면(https://nedrug.mfds.go.kr/pbp/CCBAC03/getList)의 표를 쪽마다 읽어 모은다.

    OUT=dmf_list.csv python .github/scripts/fetch_dmf.py

받은 줄 수가 화면에 적힌 '총 N건'보다 많이 모자라면 실패로 끝내고
파일을 쓰지 않는다(어제 받은 파일이 그대로 남는다).
"""
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from lxml import html

URL = "https://nedrug.mfds.go.kr/pbp/CCBAC03/getList"
OUT = os.environ.get("OUT") or "dmf_list.csv"
META = os.environ.get("META") or os.path.splitext(OUT)[0] + "_meta.json"
LIMIT = int(os.environ.get("LIMIT") or "0")  # 0 이면 큰 값부터 시험해 먹히는 값을 쓴다
MAX_PAGES = int(os.environ.get("MAX_PAGES") or "0")  # 시험용: 0 이면 끝까지
KST = timezone(timedelta(hours=9))
HDR = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept-Language": "ko-KR,ko;q=0.9"}


def cell_text(td):
    """칸 글자. 칸 안에 작은 표가 있으면(제조소가 여럿) ' / ' 로 잇는다."""
    inner = td.xpath(".//table")
    if inner:
        vals = []
        for t in inner:
            for c in t.xpath(".//td"):
                v = " ".join(c.text_content().split())
                if v and v not in vals:
                    vals.append(v)
        return " / ".join(vals)
    return " ".join(td.text_content().split())


def get(sess, page, limit, tries=4):
    params = {"page": page, "limit": limit, "searchYn": "true", "sort": "", "sortOrder": ""}
    for i in range(tries):
        try:
            r = sess.get(URL, params=params, headers=HDR, timeout=90)
            if r.status_code == 200 and "<table" in r.text:
                return r.text
            err = f"HTTP {r.status_code}"
        except Exception as e:
            err = str(e)[:120]
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"{page}쪽 받기 실패: {err}")


def parse(text):
    doc = html.fromstring(text)
    tb = doc.xpath("(//table[.//th[contains(normalize-space(.), '등록번호')]])[1]")
    if not tb:
        return None, [], []
    tb = tb[0]
    hdr = [" ".join(th.text_content().split()) for th in tb.xpath("./thead//th")]
    if not hdr:
        hdr = [" ".join(th.text_content().split()) for th in tb.xpath(".//tr[1]/th")]
    rows = []
    for tr in tb.xpath("./tbody/tr"):
        tds = tr.xpath("./td")
        if len(tds) < 5:
            continue
        r = [cell_text(td) for td in tds]
        # 좁은 화면용으로 칸마다 앞에 칸 이름이 붙어 있다('성분명가도부트롤') — 떼어 낸다
        for i, h in enumerate(hdr[:len(r)]):
            if h and r[i].startswith(h):
                r[i] = r[i][len(h):].strip()
        rows.append(r)
    m = re.search(r"총\s*([\d,]+)\s*건", doc.text_content())
    total = int(m.group(1).replace(",", "")) if m else None
    return total, hdr, rows


def main():
    sess = requests.Session()
    t0 = time.time()
    # 한 쪽에 몇 줄까지 주는지 본다 — 많이 주면 쪽 수가 줄어 빨리 끝난다
    tries = [LIMIT] if LIMIT else [1000, 500, 200, 100, 40]
    limit, total, hdr, first = None, None, None, None
    for lim in tries:
        total, hdr, rows = parse(get(sess, 1, lim))
        print(f"limit={lim} → 1쪽 {len(rows)}줄 · 화면 총 {total}건")
        if rows and (len(rows) >= min(lim, total or lim) * 0.9):
            limit, first = lim, rows
            break
        if rows and first is None:
            limit, first = len(rows), rows  # 주는 만큼만 쓴다
    if not first:
        print("표를 못 읽었다")
        return 1
    print(f"머리 {len(hdr)}칸: {' | '.join(hdr)}")
    pages = -(-(total or len(first)) // limit)
    if MAX_PAGES:
        pages = min(pages, MAX_PAGES)
    print(f"한 쪽 {limit}줄 · {pages}쪽을 받는다")
    rows = list(first)
    for pg in range(2, pages + 1):
        _, _, r = parse(get(sess, pg, limit))
        if not r:
            print(f"{pg}쪽이 비었다 — 여기서 멈춘다")
            break
        rows.extend(r)
        if pg % 20 == 0:
            print(f"   {pg}/{pages}쪽 · {len(rows):,}줄 · {time.time() - t0:.0f}초")
        time.sleep(0.4)

    width = max(len(hdr), max(len(r) for r in rows))
    hdr = (hdr + [f"칸{i + 1}" for i in range(len(hdr), width)])[:width]
    rows = [(r + [""] * width)[:width] for r in rows]
    # 순번 칸은 날마다 바뀌어 비교를 어지럽히므로 뺀다
    if hdr and hdr[0] in ("순번", "연번", "번호"):
        hdr, rows = hdr[1:], [r[1:] for r in rows]
    # 같은 줄이 쪽 경계에서 두 번 나올 수 있다
    seen, uniq = set(), []
    for r in rows:
        t = tuple(r)
        if t not in seen:
            seen.add(t)
            uniq.append(r)
    rows = uniq
    print(f"받은 줄 {len(rows):,} (중복 뺌) · 화면 총 {total}건 · {time.time() - t0:.0f}초")

    if not MAX_PAGES and total and len(rows) < total * 0.98:
        print("[실패] 받은 줄이 화면 건수보다 2% 넘게 모자란다 — 파일을 쓰지 않는다")
        return 1
    if not MAX_PAGES and len(rows) < 9000:
        print("[실패] 9,000줄보다 적다 — 화면이 바뀌었을 수 있어 파일을 쓰지 않는다")
        return 1

    # 등록번호 → 첫 칸 순으로 정렬해 날마다 같은 순서가 되게 한다(바뀐 줄만 차이가 남)
    key = hdr.index("등록번호") if "등록번호" in hdr else 0
    rows.sort(key=lambda r: (r[key], r))
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        w.writerows(rows)

    def col(name):
        return hdr.index(name) if name in hdr else None

    meta = {"fetched_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
            "source": URL, "rows": len(rows), "site_total": total}
    di = col("최초등록일자")
    if di is not None:
        ds = sorted(r[di] for r in rows if re.match(r"\d{4}-\d{2}-\d{2}", r[di] or ""))
        meta["latest_first_reg"] = ds[-1] if ds else None
        by_year = {}
        for d in ds:
            by_year[d[:4]] = by_year.get(d[:4], 0) + 1
        meta["by_year"] = dict(sorted(by_year.items())[-6:])
    ci = col("취소/취하구분")
    if ci is not None:
        st = {}
        for r in rows:
            st[r[ci] or "(빈칸)"] = st.get(r[ci] or "(빈칸)", 0) + 1
        meta["status"] = st
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))
    print(f"맨 앞 3줄:")
    for r in rows[:3]:
        print("   " + " | ".join(x[:30] for x in r))
    print(f"파일 {OUT} · {os.path.getsize(OUT) / 1e6:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
