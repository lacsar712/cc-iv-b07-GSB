import math
import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import IRR_CLASSES, connect, init_schema
from rules import equivalent_curve, judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}

SCAN_COLS = (
    "id, string_code, voc_v, isc_a, fill_factor, irr_class, status, verdict, reason, "
    "created_by, created_at, processed_at"
)


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        init_schema(conn)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "高照", "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "低照", "衰减"),
            ]
            for code, voc, isc, ff, irr, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, irr_class, status,
                        verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, irr, verdict, reason, now, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request, action: str = "提交IV扫描"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=f"仅扫描员可{action}")
    return user


def bad(detail: str):
    raise HTTPException(status_code=400, detail=detail)


def parse_equiv_payload(data) -> list[dict]:
    """解析 {items:[{id, weight}]} 或 {ids:[], weights:[]}，并做基础校验。"""
    raw_items = data.get("items")
    if isinstance(raw_items, list):
        pairs = []
        for it in raw_items:
            if not isinstance(it, dict):
                bad("折算条目格式不正确")
            pairs.append((it.get("id"), it.get("weight")))
    else:
        ids = data.get("ids") or []
        weights = data.get("weights") or []
        if not isinstance(ids, list) or not isinstance(weights, list):
            bad("请选择要折算的扫描并给出权重")
        if len(ids) != len(weights):
            bad("权重数量必须与所选扫描一致")
        pairs = list(zip(ids, weights))
    if len(pairs) < 2:
        bad("并联折算至少选择两笔扫描")
    seen = set()
    parsed = []
    for sid, weight in pairs:
        try:
            scan_id = int(sid)
            w = float(weight)
        except (TypeError, ValueError):
            bad("扫描编号与权重必须是数字")
        if not math.isfinite(w) or w <= 0:
            bad("权重必须为正数")
        if scan_id in seen:
            bad("同一笔扫描不能重复选择")
        seen.add(scan_id)
        parsed.append({"id": scan_id, "weight": w})
    return parsed


def load_equiv_sources(conn, parsed: list[dict], lock: bool) -> list[dict]:
    """按选择取扫描并执行三条业务红线：已办结、至少两笔（解析阶段已拦）、光照同类。"""
    ids = [p["id"] for p in parsed]
    suffix = " FOR UPDATE" if lock else ""
    rows = conn.execute(
        f"SELECT {SCAN_COLS} FROM iv_scans WHERE id = ANY(%s){suffix}",
        (ids,),
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    sources = []
    for p in parsed:
        row = by_id.get(p["id"])
        if row is None:
            bad(f"扫描 #{p['id']} 不存在")
        if row["status"] != "done":
            bad(f"扫描 #{p['id']} 尚未办结，不能参与折算")
        sources.append({**dict(row), "weight": p["weight"]})
    classes = {s["irr_class"] for s in sources}
    if len(classes) > 1:
        bad("高照与低照两类曲线不能混选折算")
    return sources


def build_preview(sources: list[dict]) -> dict:
    curve = equivalent_curve(sources)
    total_w = sum(s["weight"] for s in sources)
    return {
        "irr_class": sources[0]["irr_class"],
        "count": len(sources),
        "total_weight": total_w,
        "voc_v": curve["voc_v"],
        "isc_a": curve["isc_a"],
        "fill_factor": curve["fill_factor"],
        "items": [
            {
                "id": s["id"],
                "string_code": s["string_code"],
                "weight": s["weight"],
                "weight_share": s["weight"] / total_w,
                "voc_v": s["voc_v"],
                "isc_a": s["isc_a"],
                "fill_factor": s["fill_factor"],
            }
            for s in sources
        ],
    }


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(f"SELECT {SCAN_COLS} FROM iv_scans ORDER BY id DESC").fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    irr = data.get("irr_class") or "高照"
    if irr not in IRR_CLASSES:
        raise HTTPException(status_code=400, detail="光照类型只能是高照或低照")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    if not (math.isfinite(voc) and math.isfinite(isc) and math.isfinite(ff)):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是有限数字")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, irr_class, status,
                created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, irr_class, status,
                         verdict, reason, created_by, created_at, processed_at""",
            (code, voc, isc, ff, irr, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@post("/api/equiv/preview", status_code=200)
async def equiv_preview(request: Request) -> dict:
    # 旁观者同样可以看预览，只是不能折算
    need_login(request)
    parsed = parse_equiv_payload(await request.json())
    with connect() as conn:
        sources = load_equiv_sources(conn, parsed, lock=False)
        return build_preview(sources)


@post("/api/equiv/convert", status_code=201)
async def equiv_convert(request: Request) -> dict:
    user = need_writer(request, action="折算等效曲线")
    parsed = parse_equiv_payload(await request.json())
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            with conn.transaction():
                sources = load_equiv_sources(conn, parsed, lock=True)
                preview = build_preview(sources)
                irr = preview["irr_class"]
                src_ids = "+".join(str(s["id"]) for s in sources)
                # 等效新单先入队等候处理，绝不在折算时直接标成已办结
                new_row = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, irr_class, status,
                        created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
                       RETURNING id, string_code, voc_v, isc_a, fill_factor, irr_class,
                                 status, verdict, reason, created_by, created_at,
                                 processed_at""",
                    (
                        f"等效({irr}):{src_ids}",
                        preview["voc_v"],
                        preview["isc_a"],
                        preview["fill_factor"],
                        irr,
                        user["username"],
                        now,
                    ),
                ).fetchone()
                # 折算流水与新单必须同一次入账，缺一边整个事务回滚
                record = conn.execute(
                    """INSERT INTO iv_equiv_records
                       (result_scan_id, irr_class, item_count, voc_v, isc_a,
                        fill_factor, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id, result_scan_id, irr_class, item_count, voc_v,
                                 isc_a, fill_factor, created_by, created_at""",
                    (
                        new_row["id"],
                        irr,
                        preview["count"],
                        preview["voc_v"],
                        preview["isc_a"],
                        preview["fill_factor"],
                        user["username"],
                        now,
                    ),
                ).fetchone()
                for s in sources:
                    conn.execute(
                        """INSERT INTO iv_equiv_items (record_id, source_scan_id, weight)
                           VALUES (%s,%s,%s)""",
                        (record["id"], s["id"], s["weight"]),
                    )
        except HTTPException:
            raise
        result = dump(new_row)
        result["record"] = dump(record)
        result["items"] = [
            {"source_scan_id": s["id"], "weight": s["weight"]} for s in sources
        ]
        return result


@get("/api/equiv/records")
async def equiv_records(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.id, r.result_scan_id, r.irr_class, r.item_count, r.voc_v,
                      r.isc_a, r.fill_factor, r.created_by, r.created_at,
                      s.status AS result_status, s.verdict AS result_verdict
               FROM iv_equiv_records r
               JOIN iv_scans s ON s.id = r.result_scan_id
               ORDER BY r.id DESC"""
        ).fetchall()
        records = []
        for r in rows:
            items = conn.execute(
                """SELECT source_scan_id, weight
                   FROM iv_equiv_items WHERE record_id = %s ORDER BY id""",
                (r["id"],),
            ).fetchall()
            doc = dump(r)
            doc["items"] = [dict(i) for i in items]
            records.append(doc)
        return records


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        equiv_preview,
        equiv_convert,
        equiv_records,
    ]
)
