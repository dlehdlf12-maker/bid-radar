"""
입찰 레이더 - 2단계: 공고 수집 + 참가가능지역/면허제한 판정 + 첨부파일 목록

하는 일
  1) 최근 7일 공사 입찰공고 검색 (업종명 '실내건축')
  2) 같은 기간의 참가가능지역 / 면허제한 / 첨부파일 정보를 한꺼번에 받아서 공고번호로 연결
  3) 본점 지역 기준으로 참여 가능 여부 1차 판정
  4) 결과 → result.csv,  각 API 응답 샘플 → samples/ 폴더

실행
  pip install requests
  .env 파일에  SERVICE_KEY=인증키   (선택: BASE_REGION=인천)
  python step2_collect.py
"""
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

import sys

try:
    import requests
except ImportError:  # requests 미설치 시 파이썬 기본 기능(urllib)으로 대체
    import urllib.parse
    import urllib.request

    class _Resp:
        def __init__(self, code, text):
            self.status_code, self.text = code, text

        def json(self):
            return json.loads(self.text)

    class requests:  # noqa: N801  (requests.get 과 같은 모양으로 사용)
        @staticmethod
        def get(url, params=None, timeout=30):
            full = url + ("?" + urllib.parse.urlencode(params) if params else "")
            req = urllib.request.Request(full, headers={"User-Agent": "bid-radar/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return _Resp(r.status, r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                return _Resp(e.code, e.read().decode("utf-8", "replace"))

# 윈도우 콘솔(cp949)에서 이모지·특수문자 출력 시 멈추는 문제 방지
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02"
OP = {
    "search":  "getBidPblancListInfoCnstwkPPSSrch02",   # 나라장터검색조건에 의한 입찰공고공사조회
    "region":  "getBidPblancListInfoPrtcptPsblRgn02",   # 참가가능지역정보조회
    "license": "getBidPblancListInfoLicenseLimit02",    # 면허제한정보조회
    "file":    "getBidPblancListInfoAtchFileInfo02",    # 첨부파일정보조회
}
KEYWORD = "실내건축"          # 업종명 검색어
TARGET_LICENSE = "실내건축"   # 면허제한에 이 글자가 있으면 내 업종으로 판단
DAYS = 7                      # 2026-11-10부터 조회기간 최대 7일

HERE = Path(__file__).parent
SAMPLES = HERE / "samples"


# ---------------- 설정 ----------------
def load_env():
    # .env 또는 윈도우에서 확장자가 숨겨져 생긴 .env.txt 둘 다 허용
    names = [".env", ".env.txt", "env.txt", "env", ".env.example", ".env.example.txt", "env.example.txt"]
    env = next((d / n for d in (HERE, HERE.parent) for n in names if (d / n).is_file()
                and "SERVICE_KEY=" in (d / n).read_text(encoding="utf-8-sig", errors="ignore")
                and "여기에_일반인증키" not in (d / n).read_text(encoding="utf-8-sig", errors="ignore")), None)
    if env is None and os.environ.get("SERVICE_KEY"):  # GitHub Actions 등: 환경변수로 받은 키 사용
        return unquote(os.environ["SERVICE_KEY"].strip()), os.environ.get("BASE_REGION", "인천")
    if env is None:
        raise SystemExit(f"NO_ENV|인증키 파일을 찾을 수 없습니다. 위치: {HERE}")
    if env:
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip().lstrip("\ufeff")] = v.strip().strip('"').strip("'")
    key = os.environ.get("SERVICE_KEY")
    if not key:
        raise SystemExit(".env 파일에 SERVICE_KEY=인증키 를 넣어주세요.")
    return unquote(key), os.environ.get("BASE_REGION", "인천")


# ---------------- API 공통 ----------------
ERRORS = []  # 화면에 보여줄 API 오류 메시지


def period_params(days=None):
    """조회기간: (오늘-6일) 00:00 ~ 현재 시각  → 7일 한도를 넘지 않게"""
    end = datetime.now()
    start = (end - timedelta(days=(days or DAYS) - 1)).replace(hour=0, minute=0)
    return {"inqryDiv": 1, "inqryBgnDt": start.strftime("%Y%m%d%H%M"), "inqryEndDt": end.strftime("%Y%m%d%H%M")}


RETRIES = 4  # 접속 실패(타임아웃 등) 시 재시도 횟수


def get_retry(url, params, op_name="", page=1):
    """해외 서버(GitHub)에서 간헐적으로 나는 접속·SSL 타임아웃을 재시도로 흡수"""
    import time
    for i in range(RETRIES + 1):
        try:
            return requests.get(url, params=params, timeout=60)
        except Exception as e:  # URLError, 타임아웃, 연결 끊김 등
            if i == RETRIES:
                print(f"  ⚠️ [{op_name}] p{page} 최종 실패: {e!r}")
                return None
            wait = 5 * (i + 1)
            print(f"  … [{op_name}] p{page} 접속 오류({type(e).__name__}) → {wait}초 후 재시도 {i + 1}/{RETRIES}")
            time.sleep(wait)


PAGE_SIZE = 500  # 한 번에 받는 건수 (API 호출 횟수 절약). 오류 나면 자동으로 100으로 낮춤


def call_all(key, op_name, extra):
    """페이지를 끝까지 돌면서 item 목록을 모두 받음"""
    size = PAGE_SIZE
    out, page, calls = [], 1, 0
    while True:
        params = {"serviceKey": key, "pageNo": page, "numOfRows": size, "type": "json", **extra}
        r = get_retry(f"{BASE}/{OP[op_name]}", params, op_name, page)
        if r is None:  # 재시도까지 실패 → 이 항목은 여기까지만 받고 다음으로 진행
            ERRORS.append(f"[{op_name}] {page}페이지 접속 실패(재시도 {RETRIES}회) — 일부 누락 가능")
            print(f"  ⚠️ [{op_name}] {page}페이지 접속 실패 — 받은 {len(out)}건까지만 사용")
            return out
        calls += 1
        try:
            data = r.json()
        except ValueError:
            if size > 100 and page == 1:
                size = 100
                continue
            print(f"  ⚠️ [{op_name}] JSON이 아닌 응답:\n{r.text[:600]}")
            ERRORS.append(f"[{op_name}] HTTP {r.status_code} · {r.text[:300]}")
            return out
        res = data.get("response", {})
        head, body = res.get("header", {}), res.get("body", {})
        if head.get("resultCode") not in ("00", None):
            if size > 100 and page == 1:
                size = 100
                continue
            print(f"  ⚠️ [{op_name}] 오류: {head}")
            ERRORS.append(f"[{op_name}] {head.get('resultCode')} · {head.get('resultMsg')}")
            return out
        rows = body.get("items") or []
        if isinstance(rows, dict):
            rows = rows.get("item", [])
            rows = rows if isinstance(rows, list) else [rows]
        out += rows
        total = int(body.get("totalCount", 0) or 0)
        if page * size >= total or not rows:
            print(f"  [{op_name}] {len(out)}건 (API 호출 {calls}회)")
            return out
        page += 1


def save_sample(name, rows):
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / f"{name}.json").write_text(json.dumps(rows[:3], ensure_ascii=False, indent=2), encoding="utf-8")


def pick(row, *cands, contains=None):
    """필드명이 조금 달라도 찾아오기: 후보 이름 → 없으면 이름에 contains가 들어간 첫 필드"""
    for c in cands:
        if row.get(c) not in (None, ""):
            return row[c]
    if contains:
        for k, v in row.items():
            if all(s.lower() in k.lower() for s in contains) and v not in (None, ""):
                return v
    return ""


def bid_key(row):
    return (row.get("bidNtceNo", ""), str(row.get("bidNtceOrd", "")))


# ---------------- 메인 ----------------
def main():
    key, base_region = load_env()
    period = period_params()
    print(f"조회기간 {period['inqryBgnDt']} ~ {period['inqryEndDt']}  /  본점 지역: {base_region}\n")

    bids = call_all(key, "search", {**period, "indstrytyNm": KEYWORD})
    if not bids:
        print("공고가 없거나 조회 실패. 위 메시지를 확인하세요.")
        return
    regions = call_all(key, "region", period)
    licenses = call_all(key, "license", period)
    files = call_all(key, "file", period)
    for n, rows in [("search", bids), ("region", regions), ("license", licenses), ("file", files)]:
        save_sample(n, rows)

    # 공고번호별로 묶기
    rgn_map, lic_map, file_map = defaultdict(set), defaultdict(set), defaultdict(list)
    for r in regions:
        rgn_map[bid_key(r)].add(str(pick(r, "prtcptPsblRgnNm", contains=["rgn", "nm"])))
    for r in licenses:
        lic_map[bid_key(r)].add(str(pick(r, "lcnsLmtNm", "indstrytyNm", contains=["lmt", "nm"])))
    for r in files:
        name = pick(r, "atchFileNm", "ntceSpecFileNm", contains=["file", "nm"])
        url = pick(r, "atchFileUrl", "ntceSpecDocUrl", contains=["url"])
        if name or url:
            file_map[bid_key(r)].append(f"{name} | {url}")

    # 판정
    out = []
    for b in bids:
        k = bid_key(b)
        rg = sorted(x for x in rgn_map.get(k, set()) if x)
        lic = sorted(x for x in lic_map.get(k, set()) if x)
        region_ok = (not rg) or any(base_region in x or "전국" in x for x in rg)
        lic_ok = (not lic) or any(TARGET_LICENSE in x for x in lic)
        verdict = "참여 가능(1차)" if region_ok and lic_ok else "불가"
        reason = [] if verdict.startswith("참여") else (["지역"] if not region_ok else []) + (["면허"] if not lic_ok else [])
        out.append({
            "판정": verdict,
            "불가사유": ",".join(reason),
            "공고번호": f"{k[0]}-{k[1]}",
            "공고명": b.get("bidNtceNm", ""),
            "수요기관": b.get("dminsttNm", ""),
            "추정가격": b.get("presmptPrce", ""),
            "낙찰방법": pick(b, "sucsfbidMthdNm", contains=["sucsfbid", "mthd"]),
            "낙찰하한율": pick(b, "sucsfbidLwltRate", contains=["lwlt"]),
            "참가가능지역": " / ".join(rg) or "(제한없음)",
            "면허제한": " / ".join(lic),
            "입찰마감": b.get("bidClseDt", ""),
            "첨부파일": "\n".join(file_map.get(k, [])),
            "공고URL": pick(b, "bidNtceDtlUrl", "bidNtceUrl", contains=["url"]),
        })

    out.sort(key=lambda x: (x["판정"] != "참여 가능(1차)", x["입찰마감"]))
    with open(HERE / "result.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    ok = [o for o in out if o["판정"].startswith("참여")]
    print(f"\n전체 {len(out)}건 중 1차 참여 가능 {len(ok)}건\n")
    for o in ok[:15]:
        print(f"✅ {o['공고명']} | {o['수요기관']} | {o['추정가격']} | 지역:{o['참가가능지역']} | 마감 {o['입찰마감']}")
    print("\n→ result.csv (엑셀로 열기), samples/ 폴더에 API 응답 샘플 저장 완료")


if __name__ == "__main__":
    main()
