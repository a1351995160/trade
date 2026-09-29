"""按显式维护者采集清单保存 BaoStock 响应；不选策略、不计算收益、不重试。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from chanlun_trader.research.guard import ResearchDataAccessGuard


def put(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def execute(plan_path, output):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if plan.get("purpose") != "EXPLORATORY_ENGINEERING_ACCEPTANCE" or not plan.get("authorization_scope"):
        raise ValueError("EXPLICIT_ACQUISITION_SCOPE_REQUIRED")
    requests = plan["requests"]
    if not 0 < len(requests) <= plan["max_requests"] <= 9:
        raise ValueError("ACQUISITION_REQUEST_CAP")
    guard = ResearchDataAccessGuard()
    names = set()
    for item in requests:
        if item["api"] not in ("query_history_k_data_plus", "query_dividend_data", "query_adjust_factor"):
            raise ValueError("ACQUISITION_API_NOT_ALLOWED")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+[.]json", item["file"]) or item["file"] in names:
            raise ValueError("ACQUISITION_FILENAME_INVALID")
        names.add(item["file"])
        args = item["request"]
        code = args.get("code", "")
        if not re.fullmatch(r"(sz[.]00[0-9]{4}|sh[.]60[0-9]{4})", code):
            raise ValueError("ACQUISITION_MAIN_BOARD_REQUIRED")
        if item["api"] == "query_dividend_data":
            year = int(args["year"])
            guard.check_range(year*10000+101, (year+1)*10000+1231, "dividend report possible following-year events")
        else:
            guard.check_range(int(args["start_date"].replace("-", "")), int(args["end_date"].replace("-", "")), "acquisition source")
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    put(output / "ACQUISITION_PLAN.json", plan)
    import baostock as bs
    socket.setdefaulttimeout(30)
    login = bs.login()
    put(output / "LOGIN.json", {"error_code": login.error_code, "error_msg": login.error_msg,
                               "recorded_at": datetime.now(timezone.utc).isoformat()})
    if login.error_code != "0":
        put(output / "ACQUISITION_RESULT.json", {"completed": False, "responses": [], "request_count": 0,
            "stage": "LOGIN", "error_code": login.error_code, "error_msg": login.error_msg,
            "automatic_retry": False, "independent_confirmation_eligible": False})
        raise RuntimeError("BAOSTOCK_LOGIN_FAILED")
    summaries = []
    try:
        for item in requests:
            stamp = datetime.now(timezone.utc).isoformat()
            put(output / (item["file"] + ".START.json"), {**item, "started_at": stamp, "automatic_retry": False})
            result = getattr(bs, item["api"])(**item["request"])
            rows = []
            while result.error_code == "0" and result.next():
                rows.append(result.get_row_data())
            response = {"provider": "BaoStock", "api": item["api"], "request": item["request"],
                        "requested_at_utc": stamp, "received_at_utc": datetime.now(timezone.utc).isoformat(),
                        "historical_available_at_verified": False, "mode": "HISTORICAL_MODELED",
                        "fields": result.fields, "error_code": result.error_code, "error_msg": result.error_msg,
                        "raw_rows": rows, "raw_rows_sha256": hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()}
            put(output / item["file"], response)
            summaries.append({"file": item["file"], "sha256": hashlib.sha256((output/item["file"]).read_bytes()).hexdigest(),
                              "row_count": len(rows), "error_code": result.error_code})
            print(json.dumps(summaries[-1]), flush=True)
            if result.error_code != "0":
                raise RuntimeError("BAOSTOCK_RESPONSE_FAILED:" + item["file"])
    finally:
        bs.logout()
        put(output / "ACQUISITION_RESULT.json", {"responses": summaries, "completed": len(summaries) == len(requests)
                                                 and all(row["error_code"] == "0" for row in summaries),
                                                 "independent_confirmation_eligible": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    execute(args.plan, args.output)
