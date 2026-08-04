"""LINE WORKS カレンダー連携(読み取り)。

TSEG WORKSとの連携基盤。Service Account認証で登録ユーザー全員の既定
カレンダーから予定を取得する。

前提: Developer Console でアプリに OAuth スコープ「calendar」を追加して
おくこと。未追加の間はトークン取得が失敗するが、呼び出し側(朝あいさつ・
学習協力)は try/except で握るため既存機能は壊れない。

用途:
  1. 朝あいさつに「今日の予定」を添える            → today_lines()
  2. 学習協力の休暇者スキップ                      → users_on_leave()
  3. AI Q&A用スナップショット(lw-raw/calendar_events.json) → --export

使い方(デスクトップ):
    python lw_calendar.py --check            # 接続テスト(今日の予定を表示)
    python lw_calendar.py --export           # 7日分をBlobへスナップショット
    python lw_calendar.py --export --days 14
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lw_annotation_bot import (  # noqa: E402
    CLIENT_ID, CLIENT_SECRET, SERVICE_ACCOUNT, LW_TOKEN_URL,
    _load_private_key, _load_annotation_state, _load_user_names,
    _get_blob_container, logger,
)

JST = timezone(timedelta(hours=9))
LW_CAL_EVENTS_URL = "https://www.worksapis.com/v1.0/users/{user_id}/calendar/events"
CAL_BLOB_NAME = "calendar_events.json"

# 休暇系・仕事の外出系のイベント判定
LEAVE_RX = re.compile(r"休暇|有給|年休|半休|休み|忌引|欠勤|私用")
WORK_RX = re.compile(r"出張|工事|納入|納品|据付|立会|試運転|引取|搬入|搬出|客先|訪問|打合せ|打ち合わせ")

_tok: str = ""
_exp: float = 0.0


def _cal_token() -> str:
    """カレンダー専用トークン(スコープ calendar)。botトークンとは別建て。"""
    global _tok, _exp
    now = time.time()
    if _tok and now < _exp - 60:
        return _tok
    assertion = pyjwt.encode(
        {"iss": CLIENT_ID, "sub": SERVICE_ACCOUNT,
         "iat": int(now), "exp": int(now) + 3600},
        _load_private_key(), algorithm="RS256")
    resp = requests.post(
        LW_TOKEN_URL,
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
              "assertion": assertion,
              "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
              "scope": "calendar user.read"},
        timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _tok = data["access_token"]
    _exp = now + int(data.get("expires_in", 3600))
    return _tok


def _parse_naive(s: str):
    """予定の開始/終了文字列→naive datetime。失敗時None。"""
    s = (s or "")[:19]
    if not s:
        return None
    try:
        return datetime.fromisoformat(s if "T" in s else s + "T00:00:00")
    except ValueError:
        return None


def _expand_event(ev: dict, win_start: datetime, win_end: datetime) -> list[dict]:
    """繰り返し予定を期間内の発生日に展開する。単発は期間内ならそのまま。

    LW APIは繰り返し予定を「系列の初回日付+recurrence(RRULE)」で返すため、
    そのままだと年次点検などが2024年の日付のまま出てしまう(2026-08-04対応)。
    """
    s = _parse_naive(ev.get("start"))
    if s is None:
        return []
    e = _parse_naive(ev.get("end")) or s
    dur = e - s
    rules = [r.strip() for r in (ev.get("recurrence") or [])
             if isinstance(r, str) and r.strip()]
    base = {k: v for k, v in ev.items() if k != "recurrence"}
    if not rules:
        return [base] if (e >= win_start and s <= win_end) else []
    try:
        from dateutil.rrule import rrulestr
        norm = []
        for r in rules:
            if not r.upper().startswith(("RRULE", "EXDATE", "RDATE", "EXRULE", "DTSTART")):
                r = "RRULE:" + r
            # UNTILのZ(UTC)はnaive比較エラーになるため落とす(日単位精度で十分)
            r = re.sub(r"(UNTIL=[0-9T]+)Z", r"\1", r)
            norm.append(r)
        rs = rrulestr("\n".join(norm), dtstart=s, forceset=True)
        occs = rs.between(win_start - dur, win_end, inc=True)
    except Exception as ex:
        logger.warning(f"繰り返し展開失敗({ev.get('summary')}): {ex}")
        return [base] if (e >= win_start and s <= win_end) else []
    out = []
    for o in occs[:190]:
        oc = dict(base)
        if ev.get("all_day"):
            oc["start"] = o.date().isoformat()
            oc["end"] = (o + dur).date().isoformat()
        else:
            oc["start"] = o.isoformat(timespec="seconds")
            oc["end"] = (o + dur).isoformat(timespec="seconds")
        out.append(oc)
    return out


def _fetch_user_events(token: str, user_id: str, range_start: str,
                       range_end: str, win_start: datetime,
                       win_end: datetime) -> list[dict]:
    """1ユーザーの既定カレンダーから予定を取得し、繰り返しを展開して正規化する。"""
    try:
        r = requests.get(
            LW_CAL_EVENTS_URL.format(user_id=user_id),
            headers={"Authorization": f"Bearer {token}"},
            params={"fromDateTime": range_start, "untilDateTime": range_end},
            timeout=15)
        if r.status_code in (403, 404):
            return []  # カレンダー未使用ユーザー等は黙ってスキップ
        if r.status_code == 400:
            logger.warning(f"カレンダー400 ({user_id}): {r.text[:300]}")
            return []
        r.raise_for_status()
        payload = r.json() or {}
    except Exception as e:
        logger.warning(f"カレンダー取得失敗 ({user_id}): {e}")
        return []
    out: list[dict] = []
    for ev in payload.get("events", []):
        comps = ev.get("eventComponents")
        if not isinstance(comps, list):
            comps = [ev]
        for c in comps:
            st = c.get("start") or {}
            en = c.get("end") or {}
            raw = {
                "summary": (c.get("summary") or "").strip(),
                "start": st.get("dateTime") or st.get("date") or "",
                "end": en.get("dateTime") or en.get("date") or "",
                "all_day": bool(st.get("date")) and not st.get("dateTime"),
                "location": (c.get("location") or "").strip(),
                "recurrence": c.get("recurrence") or [],
            }
            out.extend(_expand_event(raw, win_start, win_end))
    return out


def fetch_all(days: int = 183) -> list[dict]:
    """登録ユーザー全員の予定(今日0:00〜+days日)を取得する。繰り返しは展開済み。
    
    LW APIは31日以内のクエリのみ受け付けるため、期間を分割してクエリ。
    """
    token = _cal_token()
    users = _load_annotation_state().get("users", [])
    names = _load_user_names()
    start_d = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    end_d = start_d + timedelta(days=days)
    win_start = start_d.replace(tzinfo=None)
    win_end = end_d.replace(tzinfo=None)
    
    events: list[dict] = []
    # API最大31日以内のクエリで複数回に分割
    chunk_days = 31
    d = start_d
    while d < end_d:
        d_end = min(d + timedelta(days=chunk_days), end_d)
        rs = d.isoformat(timespec="seconds")
        re_ = d_end.isoformat(timespec="seconds")
        for uid in users:
            for e in _fetch_user_events(token, uid, rs, re_, win_start, win_end):
                e["user_id"] = uid
                e["user_name"] = names.get(uid, "")
                events.append(e)
            time.sleep(0.1)
        d = d_end
    events.sort(key=lambda e: e.get("start") or "")
    # 分割クエリの重複除去: チャンク境界をまたぐ単発予定や、複数チャンクに
    # 返る繰り返し系列(展開窓は常に全期間)が二重に入るのを防ぐ
    seen: set = set()
    uniq: list[dict] = []
    for e in events:
        k = (e.get("user_id"), e.get("summary"), e.get("start"), e.get("end"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


def _is_on(e: dict, d: date) -> bool:
    """予定eが日付dにかかっているか(終日・時間帯どちらも)。"""
    s = (e.get("start") or "")[:10]
    en = (e.get("end") or "")[:10]
    if not s:
        return False
    ds = d.isoformat()
    if not en or en == s:
        return s == ds
    # 終日予定のendは翌日日付(排他的)のことがあるため < で判定
    return s <= ds < en if e.get("all_day") else s <= ds <= en


def today_lines(max_lines: int = 6) -> list[str]:
    """朝あいさつ用: 今日の予定サマリ行(出張・工事系 + お休み)を返す。"""
    today = datetime.now(JST).date()
    events = fetch_all(days=1)
    lines: list[str] = []
    leave_names: list[str] = []
    for e in events:
        if not _is_on(e, today) or not e["summary"]:
            continue
        name = e["user_name"] or "?"
        if LEAVE_RX.search(e["summary"]):
            if name not in leave_names:
                leave_names.append(name)
        elif WORK_RX.search(e["summary"]):
            loc = f"({e['location']})" if e["location"] else ""
            line = f"・{name}: {e['summary']}{loc}"
            if line not in lines:
                lines.append(line)
    if leave_names:
        lines.append("・お休み: " + "、".join(leave_names))
    return lines[:max_lines]


def users_on_leave(target: date | None = None) -> set[str]:
    """学習協力スキップ用: 対象日に休暇系の終日予定があるユーザーID集合。"""
    d = target or datetime.now(JST).date()
    return {
        e["user_id"] for e in fetch_all(days=1)
        if e.get("all_day") and _is_on(e, d) and LEAVE_RX.search(e["summary"] or "")
    }


def cmd_export(days: int = 183) -> int:
    """AI Q&A用スナップショットを lw-raw/calendar_events.json へ保存。"""
    events = fetch_all(days=days)
    payload = {
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "days": days,
        "count": len(events),
        "events": [{k: e.get(k, "") for k in
                    ("user_name", "summary", "start", "end", "all_day", "location")}
                   for e in events if e.get("summary")],
    }
    container = _get_blob_container()
    if container is None:
        print("ERROR: AZURE_BLOB_CONNECTION_STRING が未設定です", file=sys.stderr)
        return 1
    container.upload_blob(
        CAL_BLOB_NAME,
        json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"),
        overwrite=True)
    print(f"[DONE] カレンダー {len(payload['events'])} 件を {CAL_BLOB_NAME} へ保存"
          f"(今日から{days}日分)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LINE WORKS カレンダー連携")
    ap.add_argument("--check", action="store_true", help="接続テスト(今日の予定を表示)")
    ap.add_argument("--export", action="store_true", help="Blobへスナップショット保存")
    # 183日分(半年): 「今月の休み」「9月の予定」型の質問に答えられるように
    ap.add_argument("--days", type=int, default=183)
    args = ap.parse_args()

    if args.check:
        lines = today_lines()
        print("📅 今日の予定:")
        print("\n".join(lines) if lines else "(出張・休暇系の予定なし)")
        leave = users_on_leave()
        print(f"休暇者: {len(leave)} 名")
        return 0
    if args.export:
        return cmd_export(args.days)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
