FF_MIN = 0.72

# 光照分类：高照 / 低照。并联折算只允许同一光照类型合并。
LIGHT_TYPES = ("high", "low")
LIGHT_LABELS = {"high": "高照", "low": "低照"}
LIGHT_BY_LABEL = {v: k for k, v in LIGHT_LABELS.items()}


class FoldError(ValueError):
    """并联组折算入参不合法。"""


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor} 低于 {FF_MIN}"


def normalize_light(value) -> str:
    if isinstance(value, str):
        v = value.strip()
        if v in LIGHT_TYPES:
            return v
        if v in LIGHT_BY_LABEL:
            return LIGHT_BY_LABEL[v]
    raise FoldError("光照类型必须是高照或低照")


def build_fold(sources, raw_weights):
    """把两笔以上已办结扫描按权重折成一条等效曲线。

    sources: 数据库读出的源扫描（dict，含 id/status/light_type/voc_v/isc_a/fill_factor）。
    raw_weights: {scan_id: 拖条权重}，缺省等权 1。
    校验失败抛 FoldError；成功返回等效曲线与归一化权重。
    """
    if len(sources) < 2:
        raise FoldError("并联折算至少需要两笔已办结扫描")
    ids = [s["id"] for s in sources]
    if len(set(ids)) != len(ids):
        raise FoldError("同一笔扫描不能重复拖入")
    not_done = [s for s in sources if s["status"] != "done"]
    if not_done:
        raise FoldError("存在未办结扫描，未办结单据不能参与折算")
    lights = {s["light_type"] for s in sources}
    if len(lights) > 1:
        raise FoldError("高照与低照两类不能混选，请只保留同一光照类型")
    light_type = next(iter(lights))

    weights = {}
    for sid in ids:
        try:
            w = float(raw_weights.get(sid, 1))
        except (TypeError, ValueError):
            raise FoldError("权重必须是数字")
        if w < 0:
            raise FoldError("权重不能为负")
        weights[sid] = w
    total = sum(weights.values())
    if total <= 0:
        raise FoldError("权重之和必须大于 0")
    shares = {sid: weights[sid] / total for sid in ids}

    voc = sum(shares[s["id"]] * float(s["voc_v"]) for s in sources)
    isc = sum(shares[s["id"]] * float(s["isc_a"]) for s in sources)
    ff = sum(shares[s["id"]] * float(s["fill_factor"]) for s in sources)
    return {
        "light_type": light_type,
        "voc_v": voc,
        "isc_a": isc,
        "fill_factor": ff,
        "source_count": len(sources),
        "weights": weights,
        "shares": shares,
    }
