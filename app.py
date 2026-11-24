"""
입찰 레이더 - 웹 화면 서버

실행:  python app.py   →  브라우저에서 http://localhost:8000 접속
필요:  pip install requests,  같은 폴더에 .env / step2_collect.py / web/index.html

- 첫 접속 때 자동으로 최근 7일 공고를 수집해 data.json에 저장
- 화면 오른쪽 위 [↻ 지금 수집] 버튼으로 다시 수집
"""
import json
import os
import re
import threading
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import step2_collect as sc

HERE = Path(__file__).parent
WEB = HERE / "web"
DATA_FILE = HERE / "data.json"
PORT = 8000

# 기초금액 조회 (이름이 다르면 참고문서의 '입찰공고목록 공사기초금액조회' 이름으로 바꾸세요. 틀려도 나머지는 정상 동작)
sc.OP["base"] = "getBidPblancListInfoCnstwkBsisAmount02"

SIDO = {"서울": "서울", "인천": "인천", "경기": "경기", "부산": "부산", "대구": "대구", "광주": "광주",
        "대전": "대전", "울산": "울산", "세종": "세종", "강원": "강원", "충청북": "충북", "충북": "충북",
        "충청남": "충남", "충남": "충남", "전라북": "전북", "전북": "전북", "전라남": "전남", "전남": "전남",
        "경상북": "경북", "경북": "경북", "경상남": "경남", "경남": "경남", "제주": "제주", "전국": "전국"}


def short_region(text):
    text = str(text or "")
    for k, v in SIDO.items():
        if text.startswith(k) or k in text[:6]:
            return v
    return ""


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


KEYWORD_FILE = HERE / "keywords.txt"


def load_keywords():
    """keywords.txt: 한 줄에 하나. '-'로 시작하면 제외어, '#'은 주석"""
    inc, exc = [], []
    if KEYWORD_FILE.exists():
        for line in KEYWORD_FILE.read_text(encoding="utf-8-sig").splitlines():
            w = line.split("#", 1)[0].strip()
            if not w:
                continue
            (exc if w.startswith("-") else inc).append(w.lstrip("-").strip())
    return inc or ["실내건축", "인테리어", "리모델링", "환경개선"], exc


TARGET_SITES = {"인천", "서울", "경기"}   # 공사 현장 관심 지역
ELIGIBLE_ALLOW = {"전국", "인천"}          # 현장이 다른 지역이어도 남길 참가가능지역


def collect():
    key, _ = sc.load_env()
    sc.ERRORS.clear()

    # 이전 수집 결과 (마감 안 된 공고는 계속 유지하기 위해)
    prev, prev_meta = [], {}
    if DATA_FILE.exists():
        try:
            prev_meta = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            prev = prev_meta.get("items", [])
        except Exception:
            prev = []

    # 조회 구간: 평소엔 최근 7일. 처음(이전 데이터 없음)엔 3주치를 7일씩 나눠 받음
    weeks = 1 if prev_meta.get("backfilled") else int(os.environ.get("BACKFILL_WEEKS", "3"))
    windows, now = [], datetime.now()
    for w in range(weeks):
        e = now if w == 0 else (now - timedelta(days=7 * w)).replace(hour=23, minute=59)
        b = (e - timedelta(days=6)).replace(hour=0, minute=0)
        windows.append({"inqryDiv": 1, "inqryBgnDt": b.strftime("%Y%m%d%H%M"), "inqryEndDt": e.strftime("%Y%m%d%H%M")})
        print(f"조회기간 {windows[-1]['inqryBgnDt']} ~ {windows[-1]['inqryEndDt']}")
    period = {"inqryBgnDt": windows[-1]["inqryBgnDt"], "inqryEndDt": windows[0]["inqryEndDt"]}

    kw_inc, kw_exc = load_keywords()
    print(f"키워드 {len(kw_inc)}개 / 제외어 {len(kw_exc)}개")

    # ① 최근 7일 공사공고 전체 + 면허제한 정보
    allb, regions, licenses = [], [], []
    for wd in windows:
        allb += sc.call_all(key, "search", wd)
        regions += sc.call_all(key, "region", wd)
        licenses += sc.call_all(key, "license", wd)

    # ② 면허제한에 '실내건축'이 걸린 공고 → 무조건 포함
    lic_all = defaultdict(set)
    for r in licenses:
        v = str(sc.pick(r, "lcnsLmtNm", "indstrytyNm", contains=["lmt", "nm"]))
        if v:
            lic_all[sc.bid_key(r)].add(v)
    by_license = {k for k, v in lic_all.items() if any(sc.TARGET_LICENSE in x for x in v)}

    # ③ 공고명 키워드로 보충 (제외어가 있으면 제외)
    bids, match = [], {}
    for b in allb:
        k, name = sc.bid_key(b), str(b.get("bidNtceNm", ""))
        if k in by_license:
            match[k] = "면허"
        else:
            hit = next((w for w in kw_inc if w in name), None)
            if not hit or any(x in name for x in kw_exc):
                continue
            match[k] = "키워드:" + hit
        bids.append(b)
    print(f"→ 전체 공사공고 {len(allb)}건 중 면허 {sum(v == '면허' for v in match.values())}건"
          f" + 키워드 {sum(v != '면허' for v in match.values())}건 = {len(bids)}건")
    # 첨부파일은 공고 검색 결과(ntceSpecFileNm1~10)에 이미 들어 있어 별도 조회 생략 → 수집 시간 단축
    bases = []
    for wd in windows:
        bases += sc.call_all(key, "base", wd)
    for n, rows in [("search", bids), ("region", regions), ("license", licenses), ("base", bases)]:
        sc.save_sample(n, rows)

    rgn, lic, fil, base = defaultdict(set), defaultdict(set), defaultdict(list), {}
    for r in regions:
        v = short_region(sc.pick(r, "prtcptPsblRgnNm", contains=["rgn", "nm"]))
        if v:
            rgn[sc.bid_key(r)].add(v)
    for k, vs in lic_all.items():
        lic[k] = {re.sub(r"/.*$", "", x) for x in vs}
    rng = {}
    for r in bases:
        v = num(sc.pick(r, "bssamt", contains=["bssamt"]))
        if v:
            base[sc.bid_key(r)] = v
        e = num(r.get("rsrvtnPrceRngEndRate"))  # 예비가격 범위 (예: +2 → ±2%)
        if e:
            rng[sc.bid_key(r)] = abs(e)

    items, seen = [], set()
    for b in bids:
        k = sc.bid_key(b)
        if k in seen:
            continue
        seen.add(k)
        # 관심 지역만 남김: 현장이 인천·서울·경기이거나, 참가지역에 전국·인천이 있는 공고
        site0 = short_region(b.get("cnstrtsiteRgnNm")) or short_region(b.get("dminsttNm"))
        allow0 = rgn.get(k, set()) or {"전국"}
        if site0 not in TARGET_SITES and not (allow0 & ELIGIBLE_ALLOW):
            continue
        files = [{"name": b.get(f"ntceSpecFileNm{i}"), "url": b.get(f"ntceSpecDocUrl{i}")}
                 for i in range(1, 11) if b.get(f"ntceSpecDocUrl{i}")]
        jr = short_region(b.get("jntcontrctDutyRgnNm1"))
        joint = {"rgn": jr, "ratio": num(b.get("rgnDutyJntcontrctRt")) or 0} if jr and b.get("rgnDutyJntcontrctYn") == "Y" else None
        price = num(b.get("presmptPrce"))
        rate = num(sc.pick(b, "sucsfbidLwltRate", contains=["lwlt"]))
        site = short_region(sc.pick(b, "cnstrtsiteRgnNm", contains=["cnstrtsite"])) or short_region(b.get("dminsttNm"))
        items.append({
            "id": f"{k[0]}-{k[1]}",
            "name": b.get("bidNtceNm", ""),
            "org": b.get("dminsttNm", "") or b.get("ntceInsttNm", ""),
            "site": site,
            "allow": sorted(rgn.get(k, set())) or ["전국"],
            "price": round(price / 1e8, 4) if price else None,
            "base": round(base[k] / 1e8, 6) if k in base else None,
            "method": sc.pick(b, "sucsfbidMthdNm", contains=["sucsfbid", "mthd"]) or "",
            "rate": rate,
            "lic": " / ".join(sorted(lic.get(k, []))) or "제한 정보 없음",
            "licOk": (k in by_license) if lic.get(k) else None,
            "match": match.get(k, ""),
            "perf": {"type": "pending"},
            "brief": "pending",
            "joint": joint,
            "rng": rng.get(k),
            "posted": str(b.get("bidNtceDt", ""))[:10],
            "close": str(b.get("bidClseDt", ""))[:16],
            "files": files,
            "url": sc.pick(b, "bidNtceDtlUrl", "bidNtceUrl", contains=["url"]),
        })
    # 이전 결과 중 이번에 안 잡혔지만 아직 마감 전인 공고는 유지 (공고 후 7일 넘은 진행 중 공고 보존)
    new_ids = {i["id"] for i in items}
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M")
    kept = [i for i in prev if i.get("id") not in new_ids and (i.get("close") or "9999") >= now_s]
    items += kept
    print(f"→ 이번 수집 {len(new_ids)}건 + 이전 진행 중 공고 {len(kept)}건 유지")
    data = {"collected_at": datetime.now().strftime("%m.%d %H:%M"), "items": items,
            "raw_count": len(items), "backfilled": True, "total_notices": len(allb), "errors": sc.ERRORS[:10],
            "period": f"{period['inqryBgnDt']}~{period['inqryEndDt']}"}
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"→ 수집 완료: {len(items)}건")
    return data


LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _collect(self):
        with LOCK:
            try:
                return collect()
            except SystemExit as e:
                return {"error": str(e), "items": []}
            except Exception as e:  # 네트워크 오류 등
                return {"error": repr(e), "items": []}

    def do_GET(self):
        if self.path.startswith("/favicon"):
            self.send_response(204)
            self.end_headers()
            return
        if self.path.startswith("/api/bids"):
            if DATA_FILE.exists():
                return self._json(json.loads(DATA_FILE.read_text(encoding="utf-8")))
            return self._json(self._collect())
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/setup"):  # 화면에서 인증키 입력 → .env 저장
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            key = str(body.get("key", "")).strip()
            region = str(body.get("region", "인천")).strip() or "인천"
            if len(key) < 20:
                return self._json({"error": "인증키가 너무 짧습니다", "items": []})
            (HERE / ".env").write_text(f"SERVICE_KEY={key}\nBASE_REGION={region}\n", encoding="utf-8")
            import os
            os.environ["SERVICE_KEY"] = key
            os.environ["BASE_REGION"] = region
            return self._json(self._collect())
        if self.path.startswith("/api/refresh"):
            return self._json(self._collect())
        self.send_error(404)

    def log_message(self, fmt, *args):
        # API 요청만 콘솔에 표시 (args[0]이 문자열이 아닐 때도 안전하게)
        if args and "/api/" in str(args[0]):
            super().log_message(fmt, *args)

    def log_error(self, fmt, *args):
        pass  # favicon 등 사소한 404는 콘솔에 표시하지 않음


def self_check():
    """시작할 때 인증키·API 연결을 점검해서 콘솔에 결과 표시"""
    print("=" * 60)
    try:
        key, region = sc.load_env()
        print(f"[1] .env 읽기 OK  (키 앞부분 {key[:6]}…, 길이 {len(key)}, 본점 {region})")
    except SystemExit as e:
        print(f"[1] .env 읽기 실패: {e}")
        return
    try:
        end = datetime.now()
        r = sc.requests.get(f"{sc.BASE}/{sc.OP['search']}", params={
            "serviceKey": key, "pageNo": 1, "numOfRows": 1, "type": "json", **sc.period_params()}, timeout=20)
        print(f"[2] API 응답 HTTP {r.status_code}")
        print("    응답 앞부분:", r.text[:300].replace("\n", " "))
        if '"resultCode":"00"' in r.text.replace(" ", ""):
            print("    → 연결 성공")
        else:
            print("    → 위 응답 내용을 그대로 복사해서 보내주세요")
    except Exception as e:
        print(f"[2] API 연결 실패: {e!r}")
    print("=" * 60)


if __name__ == "__main__":
    self_check()
    url = f"http://localhost:{PORT}"
    print(f"입찰 레이더 실행 중 → {url}  (종료: Ctrl+C)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
