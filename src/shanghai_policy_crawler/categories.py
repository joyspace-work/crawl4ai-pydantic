from __future__ import annotations

import re


CATEGORY_DEFINITIONS: list[tuple[str, str | int, re.Pattern[str]]] = [
    ("人才奖励", 6, re.compile(r"人才|博士|硕士|就业|培训|人社|团队")),
    ("税收优惠", 7, re.compile(r"税|减免|加计扣除|税前|税收优惠|所得税")),
    ("荣誉奖励", 3, re.compile(r"专精特新|小巨人|质量奖|科学技术奖|荣誉|认定|名单|公示")),
    ("数字化补贴", 9, re.compile(r"数字化|智能化|信息化|两化融合|DCMM|上云")),
    ("研发补贴", 1, re.compile(r"研发|科委|科技型|技术创新|高新技术|工程技术研究中心|创新券")),
    ("资产补贴", 2, re.compile(r"固定资产|设备|场地|技改|技术改造|绿色工厂")),
    ("资质奖励", 4, re.compile(r"资质|认证|标准|贯标|知识产权|高新企业|绿色工厂")),
    ("融资补贴", 5, re.compile(r"融资|贷款|贴息|上市|北交所|挂牌|基金")),
]

CATEGORY_ORDER: list[tuple[str, str | int]] = [
    ("精选", "all"),
    *[(label, value) for label, value, _pattern in CATEGORY_DEFINITIONS],
]

DEFAULT_CATEGORY_LABELS: list[str] = [label for label, _value in CATEGORY_ORDER]


def infer_categories(text: str) -> tuple[list[str], list[str | int]]:
    labels: list[str] = []
    values: list[str | int] = []
    for label, value, pattern in CATEGORY_DEFINITIONS:
        if pattern.search(text):
            labels.append(label)
            values.append(value)
    if not labels:
        return ["精选"], ["all"]
    return labels, values
