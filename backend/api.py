import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import (
    LIGHT_LABELS,
    LIGHT_TYPES,
    FoldError,
    build_fold,
    judge,
    normalize_light,
)

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}

SCAN_COLS = (
    "id, string_code, voc_v, isc_a, fill_factor, light_type, status, verdict, reason, "
    "created_by, created_at, processed_at"
)

# 预览与入账统一精度，保证折算后新单 FF 就是预览所见的加权值。
EQ_ROUND = 4


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            # 两笔高照合格可供并联折算；低照仅一笔，凑不足两笔也无法与高照混选。
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "high", "合格"),
                ("阵列A-串07", 42.0, 9.0, 0.76, "high", "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "low", "衰减"),
            ]
            for code, voc, isc, ff, light, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, light_type,
                        status, verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, light, verdict, reason, now, now),
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


def parse_fold_body(data) -> tuple[list, dict]:
    raw_ids = data.get("ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="ids 必须是数组")
    try:
        ids = [int(x) for x in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="扫描编号必须是整数")
    raw_weights = data.get("weights") or {}
    if not isinstance(raw_weights, dict):
        raise HTTPException(status_code=400, detail="weights 必须是对象")
    weights = {}
    for key, val in raw_weights.items():
        try:
            weights[int(key)] = float(val)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="权重必须是数字")
    return ids, weights


def fold_preview_payload(conn, ids, weights) -> dict:
    rows = conn.execute(
        f"SELECT {SCAN_COLS} FROM iv_scans WHERE id = ANY(%s)",
        (ids,),
    ).fetchall()
    found = {r["id"]: r for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise FoldError(f"扫描单据不存在：{', '.join(map(str, missing))}")
    sources = [found[i] for i in ids]
    result = build_fold(sources, weights)
    return {
        "light_type": result["light_type"],
        "light_label": LIGHT_LABELS[result["light_type"]],
        "voc_v": round(result["voc_v"], EQ_ROUND),
        "isc_a": round(result["isc_a"], EQ_ROUND),
        "fill_factor": round(result["fill_factor"], EQ_ROUND),
        "source_count": result["source_count"],
        "sources": [
            {
                "scan_id": s["id"],
                "string_code": s["string_code"],
                "weight": round(result["weights"][s["id"]], EQ_ROUND),
                "weight_share": round(result["shares"][s["id"]], EQ_ROUND),
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
        rows = conn.execute(
            f"SELECT {SCAN_COLS} FROM iv_scans ORDER BY id DESC"
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    try:
        light_type = normalize_light(data.get("light_type"))
    except FoldError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, light_type, status,
                created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING """ + SCAN_COLS,
            (code, voc, isc, ff, light_type, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@post("/api/folds/preview")
async def preview_fold(request: Request) -> dict:
    # 旁观账号也可看预览，只是不能点折算。
    need_login(request)
    data = await request.json()
    ids, weights = parse_fold_body(data)
    try:
        with connect() as conn:
            return fold_preview_payload(conn, ids, weights)
    except FoldError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@post("/api/folds", status_code=201)
async def create_fold(request: Request) -> dict:
    user = need_writer(request, action="折算并联等效曲线")
    data = await request.json()
    ids, weights = parse_fold_body(data)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            with conn.transaction():
                # 行锁内复检，防止预览后单据状态被工人改变。
                rows = conn.execute(
                    f"""SELECT {SCAN_COLS} FROM iv_scans
                        WHERE id = ANY(%s) ORDER BY id FOR UPDATE""",
                    (ids,),
                ).fetchall()
                found = {r["id"]: r for r in rows}
                missing = [i for i in ids if i not in found]
                if missing:
                    raise FoldError(
                        f"扫描单据不存在：{', '.join(map(str, missing))}"
                    )
                sources = [found[i] for i in ids]
                result = build_fold(sources, weights)

                light = result["light_type"]
                eq_voc = round(result["voc_v"], EQ_ROUND)
                eq_isc = round(result["isc_a"], EQ_ROUND)
                eq_ff = round(result["fill_factor"], EQ_ROUND)
                label = LIGHT_LABELS[light]
                src_tag = "+".join(str(s["id"]) for s in sources)
                new_code = f"并联等效{label}-{src_tag}"
                # 等效新单只入队等候处理，状态保持 pending，不写结论。
                new_row = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, light_type, status,
                        created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
                       RETURNING """ + SCAN_COLS,
                    (
                        new_code,
                        eq_voc,
                        eq_isc,
                        eq_ff,
                        light,
                        user["username"],
                        now,
                    ),
                ).fetchone()

                # 折算流水头与明细随新单同一事务落账，缺一边整笔回滚。
                fold_row = conn.execute(
                    """INSERT INTO iv_folds
                       (new_scan_id, light_type, voc_v, isc_a, fill_factor,
                        source_count, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (
                        new_row["id"],
                        light,
                        eq_voc,
                        eq_isc,
                        eq_ff,
                        result["source_count"],
                        user["username"],
                        now,
                    ),
                ).fetchone()
                conn.executemany(
                    """INSERT INTO iv_fold_sources (fold_id, scan_id, weight, weight_share)
                       VALUES (%s,%s,%s,%s)""",
                    [
                        (
                            fold_row["id"],
                            s["id"],
                            result["weights"][s["id"]],
                            result["shares"][s["id"]],
                        )
                        for s in sources
                    ],
                )
        except FoldError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        conn.commit()
        out = dump(new_row)
        out["fold_id"] = fold_row["id"]
        return out


@get("/api/folds")
async def list_folds(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        folds = conn.execute(
            """SELECT f.id, f.new_scan_id, f.light_type, f.voc_v, f.isc_a, f.fill_factor,
                      f.source_count, f.created_by, f.created_at,
                      s.string_code AS new_string_code, s.status AS new_status,
                      s.verdict AS new_verdict
               FROM iv_folds f
               JOIN iv_scans s ON s.id = f.new_scan_id
               ORDER BY f.id DESC"""
        ).fetchall()
        if not folds:
            return []
        fold_ids = [f["id"] for f in folds]
        src_rows = conn.execute(
            """SELECT fs.fold_id, fs.scan_id, fs.weight, fs.weight_share,
                      sc.string_code
               FROM iv_fold_sources fs
               JOIN iv_scans sc ON sc.id = fs.scan_id
               WHERE fs.fold_id = ANY(%s)
               ORDER BY fs.id""",
            (fold_ids,),
        ).fetchall()
    sources_by_fold = {}
    for r in src_rows:
        sources_by_fold.setdefault(r["fold_id"], []).append(
            {
                "scan_id": r["scan_id"],
                "string_code": r["string_code"],
                "weight": r["weight"],
                "weight_share": r["weight_share"],
            }
        )
    out = []
    for f in folds:
        item = dump(f)
        item["light_label"] = LIGHT_LABELS.get(f["light_type"], f["light_type"])
        item["sources"] = sources_by_fold.get(f["id"], [])
        out.append(item)
    return out


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        preview_fold,
        create_fold,
        list_folds,
    ]
)
