"""매출 분석 단일제/복합제 판정을 실제 자료로 점검 (읽기만) — 매출 숫자는 적지 않는다.
IQVIA: '단일제/복합제' 칸 그대로 · UBIST: 성분 글자 판정 → 심평원 약가 자료의 '성분 수'(같은 약가코드)와 대조."""
import base64, json, os, re, sys, warnings, logging
from collections import Counter
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
    from dose_util import iqvia_combo, ubist_combo, ubist_ingredients
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    iq = bootstrap._parse_iqvia(bootstrap._download(drive, bootstrap.IQVIA_ID))
    k = iqvia_combo(iq)
    print("IQVIA 판정:", k.value_counts().to_dict(), "· 원자료 칸:", iq["단일제/복합제"].astype(str).value_counts().to_dict())
    print("  IQVIA 성분명에 '+' 있는데 단일제로 된 행:", int(((k == "단일제") & iq["성분명"].astype(str).str.contains("+", regex=False)).sum()),
          "· '+' 없는데 복합제:", int(((k == "복합제") & ~iq["성분명"].astype(str).str.contains("+", regex=False)).sum()))
    ub = bootstrap._parse_ubist(bootstrap._download(drive, bootstrap.UBIST_ID))
    u = ubist_combo(ub)
    print("\nUBIST 판정:", u.value_counts().to_dict())
    # 약가 자료와 대조
    print("약가 자료 만들기:", bootstrap._do_build(only={"price"}, force=True))
    import scout_sources as ss
    ss._ensure = lambda *a, **k: None
    pr = ss.load_price()
    code_n = {}
    for cd, n in zip(pr["제제코드"].astype(str), pr["성분수"].astype(str)):
        if cd and n and n not in ("nan", "None", ""):
            code_n[cd.strip().upper()] = n
    for cd, n in zip(pr["주성분코드"].astype(str), pr["성분수"].astype(str)):
        if cd and n and n not in ("nan", "None", "") and cd.strip().upper() not in code_n:
            code_n[cd.strip().upper()] = n
    ucode = ub["성분"].astype(str).str.extract(r"\[([0-9A-Z]{9})\]", expand=False).str.upper()
    ref = ucode.map(code_n)
    hv = ref.notna()
    ref_kind = ref.map(lambda n: "복합제" if str(n).split(".")[0] not in ("1",) else "단일제")
    agree = (ref_kind[hv] == u[hv])
    print(f"UBIST 행 {len(ub):,} 중 약가코드로 성분 수 확인 가능 {int(hv.sum()):,} · 판정 일치 {int(agree.sum()):,} ({agree.mean()*100:.1f}%)")
    dis = ub[hv & (ref_kind != u)]
    print("  어긋난 경우 (성분 글자 · 우리 판정 · 약가 성분 수) 상위:")
    cnt = Counter(zip(dis["성분"].astype(str), u[dis.index], ref[dis.index]))
    for (t, a, b), c in cnt.most_common(15):
        print(f"   {c:>4}행 | {a} | 약가 {b}개 | {t[:110]}")
    nh = ub[~hv]
    print(f"  약가코드로 못 찾은 행 {len(nh):,} · 그중 우리 판정:", u[nh.index].value_counts().to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
