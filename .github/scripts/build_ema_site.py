"""사이트 '해외 허가 비교 › 유럽 EMA' 화면이 읽을 작은 파일을 만든다 (data 가지 ema_site/ 에 저장).

EMA 공식 의약품 자료(JSON, 하루 두 번 갱신)를 받아 국내 허가(식약처 시트, 정상 품목)와 성분으로 맞춘다.
FDA 와는 비교하지 않는다. 다만 사이트 FDA 표가 같은 성분을 '국내에 있음'으로 본 것은 국내 있음으로 쓴다
(FDA 쪽 성분 사전 짝짓기가 더 정교함).
만드는 것: out/ema_site/prod.parquet (제품 한 줄씩) · sent.parquet (② 적응증 찾기용 문장) · meta.json
자료가 이상하면(제품 수가 너무 적음 등) 만들지 않는다 → 사이트는 어제 파일을 그대로 쓴다.
"""
import io
import json
import os
import re
import sys
import time

import pandas as pd

sys.path.insert(0, ".github/scripts")
import check_ema as ce                # noqa: E402  (EMA 받기·성분 짝짓기·문장 정리 재사용)
import build_fda_ind as bi            # noqa: E402  (찾기용 정규화 — 사이트 fda_view 와 같음)

OUT = os.environ.get("EMA_OUT", "out/ema_site")
MIN_AUTH = 1200        # 현재 허가 사람 약이 이보다 적으면 자료가 이상한 것으로 본다 (2026-11: 1,568)


def yes(v):
    return str(v).strip().lower() in ("yes", "true", "1")


def iso(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(d))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    df, src = ce.load_ema(ce.find_links())
    if df is None:
        print("EMA 자료를 받지 못함 — 어제 파일 유지")
        return 3
    c = {k: ce.col(df, *v) for k, v in {
        "cat": ["category"], "name": ["name_of_medicine"], "inn": ["international_non_proprietary_name_common_name"],
        "sub": ["active_substance"], "st": ["medicine_status"], "ind": ["therapeutic_indication"],
        "area": ["therapeutic_area_mesh"], "atc": ["atc_code_human"], "gen": ["generic"], "bio": ["biosimilar"],
        "orph": ["orphan_medicine"], "cond": ["conditional_approval"], "adv": ["advanced_therapy"],
        "mah": ["marketing_authorisation_developer_applicant_holder"], "date": ["marketing_authorisation_date"],
        "ecd": ["european_commission_decision_date"], "wd": ["withdrawal_expiry_revocation_lapse_of_marketing_authorisation_date"],
        "awd": ["withdrawal_of_application_date"], "ref": ["refusal_of_marketing_authorisation_date"],
        "url": ["medicine_url"], "upd": ["last_updated_date"]}.items()}
    miss = [k for k in ("cat", "name", "inn", "st", "ind", "url") if not c[k]]
    if miss:
        print("EMA 자료 칸이 바뀜:", miss, "— 어제 파일 유지")
        return 3
    df = df[df[c["cat"]].str.lower().str.contains("human")].copy()
    ce.load_u2i()
    df["_keys"] = df[c["inn"]].map(ce.moieties)
    if c["sub"]:
        e = df["_keys"].map(len) == 0
        df.loc[e, "_keys"] = df.loc[e, c["sub"]].map(ce.moieties)
    kr, kr_past = ce.korea_keys()
    if len(kr) < 2000:
        print(f"국내 허가 성분이 {len(kr):,}개뿐 — 국내 자료가 덜 읽힌 것으로 보고 만들지 않음")
        return 3
    try:
        b, _, _ = ce.get(ce.RAW + "fda_site/ing.parquet", timeout=120)
        ing = pd.read_parquet(io.BytesIO(b))
        us_state = dict(zip(ing["주성분"].str.lower(), ing["국내 상황"]))
    except Exception as ex:
        print("FDA 표 못 읽음 (국내 판정은 국내 시트만으로):", str(ex)[:80])
        us_state = {}

    def one(m):
        if m in kr or us_state.get(m) in ("국내에도 있음", "일부 제형 없음"):
            return "있음"
        return "취하·만료만" if m in kr_past else "없음"

    def kr_state(keys):
        st = [one(m) for m in keys]
        if not st:
            return "확인 못 함"
        if len(st) == 1:
            return {"있음": "국내 성분 있음", "취하·만료만": "국내 취하·만료만", "없음": "국내 성분 없음"}[st[0]]
        if tuple(sorted(keys)) in ce.KR_COMBOS:
            return "국내 같은 복합제 있음"
        if all(x == "있음" for x in st):
            return "성분은 모두 있음 (복합제 없음)"
        if any(x == "있음" for x in st):
            return "일부 성분만 있음"
        return "국내 성분 없음"

    def kind(r):
        if (c["atc"] and str(r[c["atc"]]).startswith("J07")) or re.search(r"vaccin", str(r[c["inn"]]), re.I):
            return "백신"
        if c["adv"] and yes(r[c["adv"]]):
            return "세포·유전자"
        if c["gen"] and yes(r[c["gen"]]):
            return "제네릭"
        if c["bio"] and yes(r[c["bio"]]):
            return "바이오시밀러"
        return "신약"

    ST = {"authorised": "허가", "withdrawn": "허가 후 철회", "application withdrawn": "허가 신청 철회", "refused": "허가 거절",
          "opinion": "허가 권고 (결정 전)", "lapsed": "허가 만료", "expired": "허가 만료", "revoked": "허가 취소",
          "suspended": "허가 정지"}
    rows = []
    for _, r in df.iterrows():
        status = ST.get(str(r[c["st"]]).strip().lower(), str(r[c["st"]]))
        ind = ce.clean(r[c["ind"]])
        date = iso(r[c["date"]]) if c["date"] else ""
        when = {"허가 후 철회": "wd", "허가 만료": "wd", "허가 취소": "wd", "허가 신청 철회": "awd", "허가 거절": "ref"}.get(status)
        rows.append({
            "성분": " + ".join(r["_keys"]) or str(r[c["inn"]]).lower(), "단일/복합": "복합" if len(r["_keys"]) > 1 else "단일",
            "INN": r[c["inn"]], "제품": r[c["name"]], "상태": status, "허가일": date,
            "상태 날짜": iso(r[c[when]]) if when and c[when] else "", "구분": kind(r),
            "희귀": "예" if c["orph"] and yes(r[c["orph"]]) else "", "조건부": "예" if c["cond"] and yes(r[c["cond"]]) else "",
            "회사": r[c["mah"]] if c["mah"] else "", "치료 분야": r[c["area"]] if c["area"] else "",
            "ATC": r[c["atc"]] if c["atc"] else "", "진단용": "예" if ce.DIAG.search(ind) else "",
            "국내 상황": kr_state(r["_keys"]), "적응증": ind, "주소": r[c["url"]]})
    p = pd.DataFrame(rows)
    auth = p[p["상태"] == "허가"]
    print(f"사람 약 {len(p):,} · 현재 허가 {len(auth):,} · 상태 {p['상태'].value_counts().to_dict()}")
    print("국내 상황 (현재 허가):", auth["국내 상황"].value_counts().to_dict())
    if len(auth) < MIN_AUTH:
        print(f"현재 허가가 {len(auth):,}개뿐 — 자료가 이상한 것으로 보고 만들지 않음")
        return 3
    # ② 적응증 찾기용 문장: 현재 허가 · 진단용 아님. 주의 문장·'5.1 참고'는 빼고, 위험인자 나열은 지우고, 미국식 철자로
    sent = []
    for i, r in auth[auth["진단용"] == ""].iterrows():
        for s in re.split(r"(?<=[.;])\s*(?=[A-Z])|•", r["적응증"]):
            if len(s) <= 3 or ce.DROP.search(s):
                continue
            n = bi.norm(bi.EG.sub(" ", ce.RISK.sub(" ", ce.us_spell(s))))
            if len(n) >= 4:
                sent.append({"row": i, "성분": r["성분"], "문장": s.strip()[:500], "_n": n})
    s = pd.DataFrame(sent)
    p.to_parquet(f"{OUT}/prod.parquet", index=False)
    s.to_parquet(f"{OUT}/sent.parquet", index=False)
    meta = {"built": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 9 * 3600)), "source": src,
            "ema_updated": max((iso(x) for x in df[c["upd"]]), default="") if c["upd"] else "",
            "n_auth": int(len(auth)), "n_all": int(len(p))}
    json.dump(meta, open(f"{OUT}/meta.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"저장: {OUT} · 제품 {len(p):,}줄 · 문장 {len(s):,}줄 · "
          f"{os.path.getsize(f'{OUT}/prod.parquet') / 1e6:.2f}MB + {os.path.getsize(f'{OUT}/sent.parquet') / 1e6:.2f}MB · "
          f"{time.time() - t0:.0f}초 · {meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
