"""NHIS '약가협상 완료 약제 목록'(공식) → scout_data/nego.parquet.
게시판 최신 글의 엑셀 첨부(2007~현재 누적)를 받아 캐시. 공식 사실('공단 약가협상 완료 연도')용.
주: 이 목록은 협상 '유형'(사용량-약가 연동/신규등재/조정) 구분은 없음(공단 미제공).
"""
import io, re, sys, urllib.request
from pathlib import Path
import pandas as pd

SD = Path(__file__).parent / "scout_data"; SD.mkdir(exist_ok=True)
BOARD = "https://www.nhis.or.kr/nhis/together/wbhaec05500m01.do"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": BOARD}

def _get(url, timeout=120):
    return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=timeout).read()

def main():
    # 1) 최신 게시글 번호
    html = _get(BOARD, 60).decode("utf-8", "ignore")
    art = re.search(r"articleNo=(\d+)", html)
    if not art:
        print("[FAIL] 최신 게시글 탐색 실패", file=sys.stderr); return
    art = art.group(1)
    # 2) 뷰 페이지 → 첨부 번호
    view = _get(f"{BOARD}?mode=view&articleNo={art}&article.offset=0&articleLimit=10", 60).decode("utf-8", "ignore")
    att = re.search(r"attachNo=(\d+)", view)
    if not att:
        print("[FAIL] 첨부 탐색 실패", file=sys.stderr); return
    att = att.group(1)
    # 3) 엑셀 다운로드
    data = _get(f"{BOARD}?mode=download&articleNo={art}&attachNo={att}")
    df = pd.read_excel(io.BytesIO(data), dtype=str).fillna("")
    df.columns = [str(c).split("\n")[0].strip() for c in df.columns]
    ren = {}
    for c in df.columns:
        if "연도" in c or "완료" in c: ren[c] = "연도"
        elif "제품" in c: ren[c] = "제품명"
        elif "회사" in c or "업체" in c: ren[c] = "회사명"
        elif "결과" in c: ren[c] = "협상결과"
    df = df.rename(columns=ren)
    df["_nm"] = df["제품명"].astype(str).str.replace(r"\s+", "", regex=True)
    df["연도"] = df["연도"].astype(str).str.extract(r"(\d{4})")[0]
    df[["연도", "제품명", "회사명", "협상결과", "_nm"]].to_parquet(SD / "nego.parquet", index=False)
    print(f"[OK] nego: {len(df):,}건 (art={art}) → scout_data/nego.parquet")

if __name__ == "__main__":
    main()
