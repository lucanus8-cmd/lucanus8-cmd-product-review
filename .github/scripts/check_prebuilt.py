"""2번 방안 시험: '무거운 가공은 로봇이, 사이트는 완성본 파일만 받기'.
사이트에는 아무것도 쓰지 않는다. 구글 드라이브에도 쓰지 않는다(권한만 읽어 본다). 매출 숫자는 적지 않는다.

자료마다 깨끗한 새 프로세스에서 잰다:
  A. 지금 방식: 사이트가 원본(엑셀·시트)을 받아 표로 만드는 동안 최고 메모리·시간
  B. 완성본 방식: 로봇이 만든 파일을 받아 저장만 할 때 최고 메모리·시간
그리고 완성본 파일 크기(압축 방식별), 드라이브에 완성본을 둘 수 있는지(권한)를 본다."""
import base64, io, json, os, resource, subprocess, sys, time

ROOT = os.getcwd()
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
NAMES = ["approval", "patent", "clinical", "price", "nego", "iqvia", "ubist"]
LABEL = {"approval": "허가", "patent": "특허", "clinical": "임상", "price": "약가", "nego": "공단협상",
         "iqvia": "매출 IQVIA", "ubist": "매출 UBIST"}

CHILD_A = r'''
import json, os, resource, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
os.environ["BOOT_BUILD_INPROC"] = "1"
import bootstrap
name = sys.argv[1]
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
t = time.time()
r = bootstrap._do_build_here(include_sales=name in ("iqvia", "ubist"), force=True, only={name})
print("__R__" + json.dumps({"base": base, "peak": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                            "sec": time.time() - t, "r": r}))
'''

CHILD_B = r'''
import io, json, os, resource, shutil, sys, time, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
import bootstrap          # 사이트와 같은 것을 불러온 상태에서 시작
src, dst = sys.argv[1], sys.argv[2]
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
t = time.time()
buf = io.BytesIO(open(src, "rb").read())      # 드라이브에서 받은 것과 같은 꼴(메모리에 받은 바이트)
tmp = dst + ".tmp"
with open(tmp, "wb") as f:
    f.write(buf.getbuffer())
os.replace(tmp, dst)
print("__R__" + json.dumps({"base": base, "peak": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                            "sec": time.time() - t}))
'''


def run(code, *args):
    env = dict(os.environ)
    p = subprocess.run([sys.executable, "-c", code, *args], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=1800)
    for ln in p.stdout.splitlines():
        if ln.startswith("__R__"):
            return json.loads(ln[5:])
    return {"err": (p.stderr or "")[-300:]}


def main():
    import gsheet
    info = gsheet._info()
    os.makedirs(".streamlit", exist_ok=True)
    with open(".streamlit/secrets.toml", "w") as f:
        f.write(f'gcp_service_account_b64 = "{base64.b64encode(json.dumps(info).encode()).decode()}"\n')
    import bootstrap

    print("── A. 지금 방식: 사이트가 원본을 받아 직접 만들 때 (자료마다 새 프로세스) ──")
    res = {}
    for n in NAMES:
        r = run(CHILD_A, n)
        res[n] = r
        if "err" in r:
            print(f"  {LABEL[n]:<10} 실패: {r['err'][-150:]}")
            continue
        print(f"  {LABEL[n]:<10} 시작 {r['base']:5.0f} MB → 최고 {r['peak']:6.0f} MB (+{r['peak']-r['base']:5.0f}) · {r['sec']:5.0f}초")

    print("\n── 완성본 파일 크기 (사이트가 받을 파일) ──")
    import pandas as pd
    sizes = {}
    for n in NAMES:
        p = bootstrap._cache_path(n)
        if not p.exists():
            print(f"  {LABEL[n]:<10} 파일 없음"); continue
        sz = p.stat().st_size / 1e6
        line = f"  {LABEL[n]:<10} 지금 형식 {p.suffix} {sz:6.1f} MB"
        if p.suffix == ".pkl":
            df = pd.read_pickle(p)
            for comp in ("zstd",):
                q = f"/tmp/{n}.{comp}.parquet"
                try:
                    df.to_parquet(q, compression=comp, index=False)
                    line += f" · parquet({comp}) {os.path.getsize(q)/1e6:6.1f} MB"
                except Exception as e:
                    line += f" · parquet 안 됨({type(e).__name__})"
            q = f"/tmp/{n}.pkl.gz"
            df.to_pickle(q, compression="gzip")
            line += f" · pkl.gz {os.path.getsize(q)/1e6:6.1f} MB"
            del df
        sizes[n] = sz
        print(line)

    print("\n── B. 완성본 방식: 사이트가 완성본 파일을 받아 저장만 할 때 (자료마다 새 프로세스) ──")
    for n in NAMES:
        p = bootstrap._cache_path(n)
        if not p.exists():
            continue
        r = run(CHILD_B, str(p), f"/tmp/recv_{n}{p.suffix}")
        if "err" in r:
            print(f"  {LABEL[n]:<10} 실패: {r['err'][-150:]}"); continue
        a = res.get(n, {})
        cmp_ = f" | 지금 방식 +{a['peak']-a['base']:.0f} MB → +{r['peak']-r['base']:.0f} MB" if "peak" in a else ""
        print(f"  {LABEL[n]:<10} 시작 {r['base']:5.0f} MB → 최고 {r['peak']:6.0f} MB (+{r['peak']-r['base']:5.0f}) · {r['sec']:5.1f}초{cmp_}")

    print("\n── 받은 완성본이 사이트가 직접 만든 것과 같은지 ──")
    for n in NAMES:
        p = bootstrap._cache_path(n); q = f"/tmp/recv_{n}{p.suffix}"
        if p.exists() and os.path.exists(q):
            same = open(p, "rb").read() == open(q, "rb").read()
            print(f"  {LABEL[n]:<10} {'같음' if same else '다름'}")

    print("\n── 드라이브에 완성본을 둘 수 있는지 (읽기만, 아무것도 만들지 않음) ──")
    try:
        from googleapiclient.discovery import build
        drive = build("drive", "v3", credentials=bootstrap._creds(), cache_discovery=False)
        try:
            ab = drive.about().get(fields="storageQuota").execute().get("storageQuota", {})
            lim = ab.get("limit")
            print(f"  서비스계정 자체 저장공간: {'한도 ' + str(round(int(lim)/1e9,1)) + ' GB' if lim else '정보 없음(한도 없음 또는 0)'} · 사용 {round(int(ab.get('usage', 0))/1e6,1)} MB")
        except Exception as e:
            print("  저장공간 조회 안 됨:", type(e).__name__)
        for n, fid in (("iqvia", bootstrap.IQVIA_ID), ("ubist", bootstrap.UBIST_ID)):
            m = drive.files().get(fileId=fid, fields="parents,capabilities(canEdit,canAddChildren),ownedByMe",
                                  supportsAllDrives=True).execute()
            cap = m.get("capabilities", {})
            print(f"  {LABEL[n]} 원본 파일: 서비스계정이 고칠 수 있음={cap.get('canEdit')}")
            for par in m.get("parents", []) or []:
                pm = drive.files().get(fileId=par, fields="capabilities(canAddChildren,canEdit)",
                                       supportsAllDrives=True).execute()
                pc = pm.get("capabilities", {})
                print(f"    들어 있는 폴더: 새 파일 넣기={pc.get('canAddChildren')} · 고치기={pc.get('canEdit')}")
    except Exception as e:
        print("  권한 확인 안 됨:", type(e).__name__, str(e)[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
