#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
看板B 数据流水线（纪律 B1.0 · 骄阳定制 · A股自选）
- 每日 16:00（北京时间）由 GitHub Actions 触发
- A股数据约 16:24-16:30 更新：15:50-16:40 窗口内若 asOfDate 未到今日，
  每 5 分钟重查一次 getUpdateStatus（免费），直到新鲜或超过截止时间
- 持仓快照（poolFields）
- 三个候选源榜单：穿透→合并去重→两段式快照（粗筛→幸存者补字段）
输出: data/<asOfDate>.json + meta.json
余额不足或接口失败立即终止（不重试付费接口）。
"""
import json, pathlib, urllib.request, urllib.parse, datetime, sys, time, os

BASE = pathlib.Path(__file__).resolve().parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
API = "https://www.trendtrader.cn/apiData/data/"
KEY = os.environ.get("TREND_API_KEY") or CFG["apiKey"]


def bj_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def call(endpoint, **params):
    qs = urllib.parse.urlencode({"apiKey": KEY, **params})
    req = urllib.request.Request(API + endpoint + "?" + qs,
                                 headers={"User-Agent": "trend-dashboard-b/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def must_ok(d, endpoint):
    if d.get("code") != "00000":
        msg = (d.get("msg") or "")
        if "余额" in msg or "balance" in msg.lower():
            print("INSUFFICIENT_BALANCE: %s" % msg)
        else:
            print("API_ERROR %s: %s" % (endpoint, msg))
        sys.exit(2)


def balance():
    d = call("getAccountBalance", viewLevel="summary")
    must_ok(d, "getAccountBalance")
    return float(d["data"][0]["balance"])


def snapshot(tmids, fields):
    if not tmids:
        return []
    d = call("getTickerSnapshot", tmIds=",".join(map(str, tmids)),
             fields=",".join(fields))
    must_ok(d, "getTickerSnapshot")
    return d.get("data", [])


def penetrate(board_id):
    d = call("getComponentTicker", tmId=board_id)
    must_ok(d, "getComponentTicker")
    return d.get("data", [])


def get_a_share_status():
    """返回 (asOfDate, updateDt)。getUpdateStatus 免费。"""
    d = call("getUpdateStatus")
    must_ok(d, "getUpdateStatus")
    target = CFG.get("freshness", {}).get("asset", "A股")
    for r in d.get("data", []):
        if r.get("asset") == target:
            return r.get("asOfDate"), r.get("updateDt")
    return None, None


def wait_fresh():
    """16:00 触发时 A股数据通常尚未更新（约16:24-16:30）。
    仅在 waitWindowStart~waitDeadline（北京时间工作日）内等待，
    每 retryIntervalSec 重查一次（免费接口）。返回 (asOfDate, updateDt, waited, staleFlag)。"""
    fr = CFG.get("freshness", {})
    t0 = fr.get("waitWindowStart", "15:50")
    t1 = fr.get("waitDeadline", "16:40")
    interval = fr.get("retryIntervalSec", 300)
    waited = 0
    as_of, upd = get_a_share_status()
    while True:
        now = bj_now()
        today = now.strftime("%Y-%m-%d")
        hm = now.strftime("%H:%M")
        is_weekday = now.weekday() < 5
        fresh = (as_of == today)
        # 非工作日 / 不在等待窗口 / 数据已新鲜 / 超过截止时间 → 停止等待
        if fresh or not is_weekday or not (t0 <= hm <= t1):
            stale = is_weekday and not fresh and hm > t1
            return as_of, upd, waited, stale
        print("A股 asOfDate=%s 未到今日 %s，%ds 后重查（免费）..." % (as_of, today, interval))
        time.sleep(interval)
        waited += 1
        as_of, upd = get_a_share_status()


def snapshot_smart(ids):
    """两段式筛选：先用 preFilterFields（温度前后+强度+节气）粗筛，
    幸存者再补 candidateFields（行业温度/市值/成交额/价格/右侧/危险信号）。
    合取条件，粗筛只剔除必然不合格者，无漏判。返回 {tmId: row}。"""
    if not ids:
        return {}
    pre = snapshot(ids, CFG["preFilterFields"])
    F = CFG["filters"]
    survivors = [r["tmId"] for r in pre
                 if (r.get("trendTemperaturePrev") == "温"
                     and r.get("trendTemperatureCurr") == "热"
                     and (r.get("trendStrengthLocalCurr") or 0) > F["strengthMin"]
                     and (r.get("trendPhaseCurr") or "") in F["phases"])]
    if not survivors:
        return {}
    pre_by_id = {r["tmId"]: r for r in pre}
    merged = {}
    for r in snapshot(survivors, CFG["candidateFields"]):
        m = dict(pre_by_id.get(r["tmId"], {}))
        m.update(r)
        merged[r["tmId"]] = m
    return merged


def main():
    held = CFG["held"]
    boards_daily = CFG.get("boardsDaily", [])
    board_names = CFG.get("boardNames", {})

    (BASE / "data").mkdir(exist_ok=True)

    bal0 = balance()
    as_of_status, upd_dt, waited, stale = wait_fresh()
    print("UpdateStatus: A股 asOf=%s updateDt=%s waited=%d stale=%s"
          % (as_of_status, upd_dt, waited, stale))

    # ---- 持仓快照 + 数据日期校验
    # getUpdateStatus 可能在实际数据就绪前就报告 fresh
    # 取到数据后验证 asOfDate 是否匹配 freshness 日期，不匹配则等待重试
    pools = []
    if held:
        max_verify = 3
        verify_interval = CFG.get("freshness", {}).get("retryIntervalSec", 300)
        for attempt in range(max_verify + 1):
            pools = snapshot(held, CFG["poolFields"])
            # 用全池最大 asOfDate 判定新鲜度：个别品种（如停牌）自身日期滞后，
            # 取 pools[0] 会把停牌品种误当全市场数据日期
            row_dates = [r.get("asOfDate") for r in pools if r.get("asOfDate")]
            actual_asof = max(row_dates) if row_dates else None
            if not as_of_status or actual_asof == as_of_status or attempt == max_verify:
                if actual_asof and as_of_status and actual_asof != as_of_status:
                    print("WARNING: data asOf=%s != freshness asOf=%s after %d retries, using actual"
                          % (actual_asof, as_of_status, max_verify))
                    stale = True
                break
            print("Verify: data asOf=%s != freshness asOf=%s, retry %d/%d in %ds..."
                  % (actual_asof, as_of_status, attempt + 1, max_verify, verify_interval))
            time.sleep(verify_interval)

    # ---- 候选源：三榜穿透→合并去重→智能快照
    daily_ids, sources = [], {}
    for b in boards_daily:
        bname = board_names.get(str(b), str(b))
        for x in penetrate(b):
            tid = x["tmId"]
            if tid not in sources:
                daily_ids.append(tid)
                sources[str(tid)] = []
            if bname not in sources[str(tid)]:
                sources[str(tid)].append(bname)
    print("候选源穿透：%d 只（去重后）" % len(daily_ids))
    candidates = snapshot_smart(daily_ids)

    bal1 = balance()
    cand_list = list(candidates.values())
    # 数据日期标签以 getUpdateStatus 的 A股 asOfDate 为准（个别品种停牌会滞后）
    as_of = as_of_status or "unknown"
    if as_of == "unknown":
        for src in (pools, cand_list):
            dates = [r.get("asOfDate") for r in src if r.get("asOfDate")]
            if dates:
                as_of = max(dates)
                break

    out = {"asOfDate": as_of, "updateDt": upd_dt, "staleData": bool(stale),
           "discipline": CFG["disciplineVersion"],
           "heldIds": held, "heldNotes": CFG.get("heldNotes", {}),
           "pools": pools, "candidates": cand_list, "sources": sources}
    (BASE / "data" / ("%s.json" % as_of)).write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")

    meta = {"cost": round(bal0 - bal1, 3), "balance": round(bal1, 3),
            "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    (BASE / "meta.json").write_text(json.dumps(meta, ensure_ascii=False),
                                    encoding="utf-8")
    print("OK asOf=%s pools=%d cand=%d cost=%.3f bal=%.3f"
          % (as_of, len(pools), len(cand_list), meta["cost"], bal1))


if __name__ == "__main__":
    main()
