FF_MIN = 0.72


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor} 低于 {FF_MIN}"


def equivalent_curve(items: list[dict]) -> dict:
    """把两笔以上已办结扫描按权重折成一条等效曲线。

    并联：开路电压取加权值，短路电流为各支路加权之和，填充因子同样按权重加权。
    items: [{"voc_v","isc_a","fill_factor","weight"}, ...]，权重为正数。
    """
    total_w = sum(float(it["weight"]) for it in items)
    ff = sum(float(it["fill_factor"]) * float(it["weight"]) for it in items) / total_w
    voc = sum(float(it["voc_v"]) * float(it["weight"]) for it in items) / total_w
    isc = sum(float(it["isc_a"]) * float(it["weight"]) for it in items) / total_w
    return {"voc_v": voc, "isc_a": isc, "fill_factor": ff}
