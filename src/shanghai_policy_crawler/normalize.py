import os
import json
from typing import Optional, Dict, List

# Load data
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(CURRENT_DIR, "china_regions.json")

class RegionNormalizer:
    def __init__(self):
        self.province_map = {}  # alias -> full_name
        self.city_map = {}      # alias -> (province_full, city_full)
        self.area_map = {}      # alias -> list of (province_full, city_full, area_full)
        self._load_data()
        
    def _clean_name(self, name: str) -> str:
        # 去掉省、市、区、县等后缀作为别名
        if len(name) > 2:
            for suffix in ["自治区", "自治州", "新区", "盟", "特区", "林区"]:
                if name.endswith(suffix):
                    return name[:-len(suffix)]
            for suffix in ["省", "市", "区", "县", "旗"]:
                if name.endswith(suffix):
                    return name[:-1]
        return name

    def _load_data(self):
        if not os.path.exists(JSON_PATH):
            return
            
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # 特殊自治区简称映射
        special_provinces = {
            "内蒙古自治区": "内蒙古",
            "新疆维吾尔自治区": "新疆",
            "西藏自治区": "西藏",
            "宁夏回族自治区": "宁夏",
            "广西壮族自治区": "广西"
        }
        
        for province_name, cities in data.items():
            # 1. 注册省份
            self.province_map[province_name] = province_name
            clean_prov = self._clean_name(province_name)
            self.province_map[clean_prov] = province_name
            if province_name in special_provinces:
                self.province_map[special_provinces[province_name]] = province_name
                
            for city_name, areas in cities.items():
                # 对于直辖市，其城市名为 "市辖区"，我们特殊处理，映射到直辖市名本身
                is_direct = province_name in ["北京市", "上海市", "天津市", "重庆市"]
                actual_city_name = province_name if is_direct else city_name
                
                # 2. 注册城市
                if not is_direct:
                    self.city_map[city_name] = (province_name, city_name)
                    clean_city = self._clean_name(city_name)
                    self.city_map[clean_city] = (province_name, city_name)
                else:
                    self.city_map[province_name] = (province_name, province_name)
                    self.city_map[self._clean_name(province_name)] = (province_name, province_name)
                
                for area_name in areas:
                    # 3. 注册区县
                    if area_name not in self.area_map:
                        self.area_map[area_name] = []
                    self.area_map[area_name].append((province_name, actual_city_name, area_name))
                    
                    clean_area = self._clean_name(area_name)
                    # 长度大于 1 避免 "县", "区" 等单字成为别名
                    if len(clean_area) > 1:
                        if clean_area not in self.area_map:
                            self.area_map[clean_area] = []
                        self.area_map[clean_area].append((province_name, actual_city_name, area_name))

    def normalize(self, region_text: Optional[str], collect_dept_name: Optional[str]) -> str:
        combined = f"{region_text or ''} {collect_dept_name or ''}"
        if not combined.strip():
            return "全国"
            
        # 特殊处理临港和浦东
        if "临港" in combined:
            return "上海市/上海市/浦东新区"
            
        # 1. 区县级匹配 (最精确)
        matched_areas = []
        # 按键长度降序排序，先匹配长的名称（比如“朝阳区”先于“朝阳”匹配），防止子串截断
        sorted_area_keys = sorted(self.area_map.keys(), key=len, reverse=True)
        for key in sorted_area_keys:
            if key in combined:
                matched_areas = self.area_map[key]
                break
                
        if matched_areas:
            # 如果有多个候选，进行消歧义
            if len(matched_areas) > 1:
                for prov, city, area in matched_areas:
                    # 如果 combined 包含省份或城市名称，则优先匹配
                    clean_prov = self._clean_name(prov)
                    clean_city = self._clean_name(city)
                    if clean_prov in combined or clean_city in combined:
                        return f"{prov}/{city}/{area}"
            # 默认返回第一个候选
            prov, city, area = matched_areas[0]
            return f"{prov}/{city}/{area}"
            
        # 2. 城市级匹配
        sorted_city_keys = sorted(self.city_map.keys(), key=len, reverse=True)
        for key in sorted_city_keys:
            if key in combined:
                prov, city = self.city_map[key]
                return f"{prov}/{city}"
                
        # 3. 省份级匹配
        sorted_prov_keys = sorted(self.province_map.keys(), key=len, reverse=True)
        for key in sorted_prov_keys:
            if key in combined:
                prov = self.province_map[key]
                if prov in ["北京市", "上海市", "天津市", "重庆市"]:
                    return f"{prov}/{prov}"
                return prov
                
        return "全国"

# Singleton instance
_normalizer = RegionNormalizer()

def normalize_region(region_text: Optional[str], collect_dept_name: Optional[str]) -> str:
    return _normalizer.normalize(region_text, collect_dept_name)
