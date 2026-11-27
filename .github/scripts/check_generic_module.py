"""사이트에 넣을 generic_entry.py 를 실제 자료로 72개 성분에 돌려 본다 (읽기만, 사이트에 쓰지 않음). 공개 자료만."""
import base64, json, os, re, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
ROOT = os.getcwd(); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
import gsheet  # noqa: E402


def main():
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import pandas as pd
    import bootstrap
    print("자료 만들기:", bootstrap._do_build(only={"approval", "price"}, force=True))
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    import generic_entry as ge
    from check_generic_simple import DEFAULT
    t0 = time.time()
    p = ge.prep_price(ss.load_price()); bulk = ge.bulk_dates(p)
    a = ge.prep_approval(ss.load_approval(), ss.ing_core, ss._ap_date)
    opts = ge.ingredient_options(a)
    print(f"준비 {time.time()-t0:.1f}초 · 약가 {len(p):,}행 {p.memory_usage(deep=True).sum()/1e6:.0f}MB · "
          f"성분 목록 {len(opts):,}개 (복합 조합 {sum(1 for o in opts if ' + ' in o):,})")
    tt = []
    for term in DEFAULT.split(";"):
        t1 = time.time()
        r = ge.analyze(p, a, bulk, term.replace("+", " + "), ss.ing_core)
        tt.append(time.time() - t1)

        def head(df):
            if df.empty:
                return "없음"
            k = df[~df["날짜모름"]]
            pre = int((df["등재일"] <= pd.Timestamp("2010-02-01")).sum())
            s = f"{len(df)}개"
            if pre:
                s += f" · 2010전 {pre}"
            if not k.empty:
                s += f" · 첫 {k['등재일'].min().date()} {ge.brand(k.iloc[0]['제품명'])}"
            if "비고" in df.columns and (df["비고"] == "오리지널 회사").any():
                s += f" (오리지널회사 {int((df['비고'] == '오리지널 회사').sum())})"
            return s
        warn = ", ".join(w["brand"] for w in r["warn"][:4])
        print(f"{term:<24} | {('/'.join(map(str, r['chosen'])) if r['chosen'] else '-')[:22]:<22} {r['reason']:<6}"
              f" | ◆ {head(r['same']):<34} | ◇ {head(r['salt']):<30} | 복합제외 {r['n_combo']:<4}"
              f" | ⚠ {warn or '-'} {('· ' + r['note']) if r['note'] else ''}")
    print(f"\n성분 하나 계산 시간: 평균 {sum(tt)/len(tt):.2f}초 · 최대 {max(tt):.2f}초")
    return 0


if __name__ == "__main__":
    sys.exit(main())
