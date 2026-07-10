"""
models.py — Pydantic data models for Shanghai policy crawling pipeline.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProjectItem(BaseModel):
    """申报项目（上海市企业创新港政策申报项目）"""
    url: str
    project_id: str
    policy_id: str
    policy_basis: str
    policy_ids: List[str] = Field(default_factory=list)
    title: str
    region: str
    agency: str
    recipient: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    content: str
    policy_object: str
    condition_text: str
    support_text: str
    material_text: str
    contact_information: str
    apply_method: str
    crawled_at: str
    raw_detail: Dict[str, Any]


class PolicyItem(BaseModel):
    """政策文件（上海市政府政策）"""
    url: Optional[str]
    policy_id: str
    project_ids: List[str] = Field(default_factory=list)
    title: str
    region: str
    document_number: str
    agency: str
    publish_date: Optional[str] = None
    expire_date: Optional[str] = None
    content: str
    pdf_url: Optional[str] = None
    raw_detail: Dict[str, Any]
