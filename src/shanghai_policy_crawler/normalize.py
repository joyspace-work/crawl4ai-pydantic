"""
normalize.py — Region normalization using cnloc.

Converts raw region strings (e.g. "上海市浦东新区", "临港新片区", "浙江省杭州市")
into a canonical province/city[/district] format:
  - 直辖市 (Beijing, Shanghai, Tianjin, Chongqing): "上海市/上海市" or "上海市/上海市/浦东新区"
  - 普通省市: "浙江省/杭州市" or "浙江省/杭州市/西湖区"
  - Unknown: "全国"
"""
from __future__ import annotations

from typing import Optional

import cnloc


# Municipalities that are both province and city
_DIRECT_CITIES = {"上海市", "北京市", "天津市", "重庆市"}

# Quick overrides for known abbreviations / special zones
_ALIASES: dict[str, str] = {
    "临港": "上海市/上海市/浦东新区",
    "临港新片区": "上海市/上海市/浦东新区",
    "浦东": "上海市/上海市/浦东新区",
    "全市": "上海市/上海市",
    "本市": "上海市/上海市",
    "沪": "上海市/上海市",
}


def normalize_region(region_text: Optional[str]) -> str:
    """Return a canonical region string for *region_text*."""
    if not region_text or not isinstance(region_text, str):
        return "全国"

    text = region_text.strip()
    if not text:
        return "全国"

    # Fast-path: known aliases
    for alias, canonical in _ALIASES.items():
        if alias in text:
            return canonical

    try:
        df = cnloc.getlocation(text)
        if df is None or len(df) == 0:
            return "全国"

        row = df.iloc[0]

        def _get(col: str) -> Optional[str]:
            val = row.get(col)
            if val is None:
                return None
            val_str = str(val).strip()
            return val_str if val_str and val_str.lower() != "nan" else None

        prov = _get("province_name")
        city = _get("city_name")
        area = _get("county_name")

        if not prov:
            return "全国"

        if prov in _DIRECT_CITIES:
            if area:
                return f"{prov}/{prov}/{area}"
            return f"{prov}/{prov}"
        else:
            if area and city:
                return f"{prov}/{city}/{area}"
            elif city:
                return f"{prov}/{city}"
            return prov

    except Exception:
        return "全国"
