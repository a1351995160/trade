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


def _validate_response_dates(item, fields, rows, guard):
    """原响应落盘前检查所有实际日期，不过滤或保存封存范围的原件。"""
    indices = [(index, name) for index, name in enumerate(fields)
               if name == 'date' or name.endswith('Date')]
    args = item['request']
    operate = item['api'] == 'query_dividend_data' and args.get('yearType', 'report') == 'operate'
    for row in rows:
        if len(row) != len(fields):
            raise ValueError('ACQUISITION_RESPONSE_SHAPE_INVALID')
        if operate and ('dividOperateDate' not in fields
                        or not row[fields.index('dividOperateDate')]):
            raise ValueError('ACQUISITION_DIVIDEND_OPERATION_DATE_MISSING')
        for index, name in indices:
            if not row[index]:
                continue
            value = int(datetime.strptime(str(row[index]).replace('-', ''), '%Y%m%d').strftime('%Y%m%d'))
            guard.check_range(value, value, 'BaoStock acquisition actual response date')
            if operate and name == 'dividOperateDate' and value // 10000 != int(args['year']):
                raise ValueError('ACQUISITION_DIVIDEND_OPERATION_YEAR_CONFLICT')


def validate_plan(plan):
    """只读检查维护者采集范围；不创建目录或连接服务。"""
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
        if not re.fullmatch(r"(sz[.](?:00|30)[0-9]{4}|sh[.]60[0-9]{4})", code):
            raise ValueError("ACQUISITION_SUPPORTED_BOARD_REQUIRED")
        if item["api"] == "query_dividend_data":
            if (not re.fullmatch(r'[0-9]{4}', str(args.get('year', '')))
                    or args.get('yearType', 'report') not in {'report', 'operate'}):
                raise ValueError('ACQUISITION_DIVIDEND_YEAR_TYPE_INVALID')
            year = int(args["year"])
            last_year = year if args.get('yearType', 'report') == 'operate' else year + 1
            guard.check_range(year*10000+101, last_year*10000+1231, "dividend declared year semantics")
        else:
            guard.check_range(int(args["start_date"].replace("-", "")), int(args["end_date"].replace("-", "")), "acquisition source")
    return guard


def execute(plan_path, output, *, connected_client=None):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    guard = validate_plan(plan)
    requests = plan['requests']
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    put(output / "ACQUISITION_PLAN.json", plan)
    if connected_client is None:
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
    else:
        bs = connected_client
        put(output / "LOGIN.json", {'connection_mode': 'TRUSTED_COLLECTOR_SESSION',
                                    'recorded_at': datetime.now(timezone.utc).isoformat()})
    summaries, request_count = [], 0
    try:
        for item in requests:
            stamp = datetime.now(timezone.utc).isoformat()
            put(output / (item["file"] + ".START.json"), {**item, "started_at": stamp, "automatic_retry": False})
            request_count += 1
            result = getattr(bs, item["api"])(**item["request"])
            rows = []
            while result.error_code == "0" and result.next():
                rows.append(result.get_row_data())
            _validate_response_dates(item, result.fields, rows, guard)
            response = {"provider": "BaoStock", "api": item["api"], "request": item["request"],
                        "requested_at_utc": stamp, "received_at_utc": datetime.now(timezone.utc).isoformat(),
                        "historical_available_at_verified": False, "mode": "HISTORICAL_MODELED",
                        "fields": result.fields, "error_code": result.error_code, "error_msg": result.error_msg,
                        "raw_rows": rows, "raw_rows_sha256": hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()}
            put(output / item["file"], response)
            summaries.append({"file": item["file"], "sha256": hashlib.sha256((output/item["file"]).read_bytes()).hexdigest(),
                              "row_count": len(rows), "error_code": result.error_code})
            if connected_client is None:
                print(json.dumps(summaries[-1]), flush=True)
            if result.error_code != "0":
                raise RuntimeError("BAOSTOCK_RESPONSE_FAILED:" + item["file"])
    finally:
        if connected_client is None:
            bs.logout()
        put(output / "ACQUISITION_RESULT.json", {"responses": summaries, "completed": len(summaries) == len(requests)
                                                 and all(row["error_code"] == "0" for row in summaries),
                                                 'request_count': request_count, 'automatic_retry': False,
                                                 "independent_confirmation_eligible": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    execute(args.plan, args.output)
