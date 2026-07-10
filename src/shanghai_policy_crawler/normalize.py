import pandas as pd
import cnloc
from typing import Optional

def normalize_region(region_text: Optional[str]) -> str:
    if not region_text or not isinstance(region_text, str):
        return "全国"
        
    text = region_text.strip()
    if not text:
        return "全国"
        
    # 对特定的特区或简称进行预处理（如临港）
    if "临港" in text:
        return "上海市/上海市/浦东新区"
        
    try:
        # cnloc.getlocation 接受地址字符串并返回 DataFrame
        df = cnloc.getlocation(text)
        if df.empty:
            return "全国"
            
        row = df.iloc[0]
        # 提取省、市、区
        prov = row['province_name'] if not pd.isna(row['province_name']) else None
        city = row['city_name'] if not pd.isna(row['city_name']) else None
        area = row['county_name'] if not pd.isna(row['county_name']) else None
        
        if not prov:
            return "全国"
            
        # 处理直辖市
        is_direct = prov in ["上海市", "北京市", "天津市", "重庆市"]
        
        if is_direct:
            if area:
                return f"{prov}/{prov}/{area}"
            else:
                return f"{prov}/{prov}"
        else:
            if area:
                return f"{prov}/{city}/{area}"
            elif city:
                return f"{prov}/{city}"
            else:
                return prov
                
    except Exception:
        # 降级处理
        return "全国"
