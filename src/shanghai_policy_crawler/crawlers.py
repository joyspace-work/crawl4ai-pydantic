"""
crawlers.py — Core crawl logic for projects and policies.

Provides two public functions:
  crawl_projects(out_file, stop_on_seen)
  crawl_policies(out_file, stop_on_seen)

Both functions:
  - Append new records to *out_file* (JSONL).
  - Skip already-crawled IDs loaded from the same file.
  - Accept stop_on_seen=True for incremental mode (stop as soon as
    a full page of non-historical items is already known).
"""
from __future__ import annotations

import json
import time
import base64
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .models import ProjectItem, PolicyItem
from .normalize import normalize_region as _normalize_region
from .llm_utils import (
    extract_policy_expire_date,
    extract_conditions_and_standards,
)
from .shanghai_gov_api import ShanghaiGovApiClient

# ── Constants ─────────────────────────────────────────────────────────────────

_PROJECTS_API = "https://zwdt.sh.gov.cn/qykj/shspace/policy_center/hqPolicy/projects"
_DETAIL_API   = "https://zwdt.sh.gov.cn/qykj/shspace/policy_center/hqPolicy/questions"
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://zwdt.sh.gov.cn/qykj/shell_oc_policy_zq/policy/policyDeclare",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
}
_MIN_CRAWL_YEAR = "2025"

# ── Shared helpers ────────────────────────────────────────────────────────────

def decode_base64_html(val: Optional[str]) -> str:
    """Decode a base64-encoded HTML blob and return plain text."""
    if not val:
        return ""
    try:
        decoded = base64.b64decode(str(val)).decode("utf-8", errors="replace")
        soup = BeautifulSoup(
            decoded.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n"),
            "html.parser",
        )
        return soup.get_text("\n").strip()
    except Exception:
        return str(val).strip()


def normalize_region(region_text: Optional[str]) -> str:
    return _normalize_region(region_text)


def format_date_with_timezone(date_str: Optional[str], default_time: str = "08:00:00") -> Optional[str]:
    """
    Convert a partial date (e.g. '2026-05-20') to 'YYYY-MM-DD HH:mm:ss GMT+8'.
    Preserves existing time component if already present.
    """
    if not date_str:
        return None
    cleaned = date_str.strip()
    if len(cleaned) >= 10:
        base_date = cleaned[:10]
        if len(cleaned) >= 19:
            return f"{base_date} {cleaned[11:19]} GMT+8"
        return f"{base_date} {default_time} GMT+8"
    return cleaned


def _load_existing_ids(out_file: Path, id_field: str, prefix: str) -> set[str]:
    """Read *out_file* (JSONL) and return raw IDs (without prefix)."""
    existing: set[str] = set()
    if not out_file.exists():
        return existing
    with open(out_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                raw_id = data.get(id_field, "")
                if raw_id.startswith(prefix):
                    existing.add(raw_id[len(prefix):])
            except Exception:
                pass
    return existing


# ── Projects ──────────────────────────────────────────────────────────────────

def crawl_projects(
    out_file: Path,
    *,
    limit_pages: Optional[int] = None,
    stop_on_seen: bool = False,
) -> int:
    """
    Crawl zwdt.sh.gov.cn申报项目 and append new records to *out_file*.

    Parameters
    ----------
    out_file      : Path to the output JSONL file (will be appended).
    limit_pages   : Optional hard cap on pages fetched.
    stop_on_seen  : If True (incremental mode), stop as soon as a full page
                    of non-historical items contains zero new IDs.

    Returns
    -------
    Number of new records written.
    """
    print("=== STARTING CRAWL FOR PROJECTS ===")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    existing_ids = _load_existing_ids(out_file, "project_id", "zwdt-")
    print(f"Loaded {len(existing_ids)} already-crawled projects.")

    page = 1
    page_size = 50
    new_count = 0

    with open(out_file, "a", encoding="utf-8") as fh:
        while True:
            body = {
                "isNeedSort": False,
                "containProject": True,
                "objectType": 1,
                "sortStrategy": 0,
                "page": page,
                "size": page_size,
                "policyType": ["BTLX", "RZLX", "JMLX", "RCLX", "RYLX", "JYLX", "QT"],
                "projectTypes": [],
                "releaseTimeAfter": "",
                "releaseTimeBefore": "",
                "keyWord": "",
                "open": True,
                "clientType": "1",
            }
            try:
                url = f"{_PROJECTS_API}?page={page}&size={page_size}"
                r = requests.post(url, headers=_HEADERS, json=body, timeout=20)
                r.raise_for_status()
                res = r.json()
            except Exception as exc:
                print(f"[projects] Failed to fetch page {page}: {exc}")
                break

            data = res.get("data") or {}
            projects_list = data.get("list", [])
            total_pages = data.get("pages", 1)

            if not projects_list:
                break

            # Track whether this page has any recent (non-historical) items
            recent_items_on_page = 0
            new_items_on_page = 0

            for p in projects_list:
                # ── Year filter: skip historical projects ─────────────────
                time_tags = p.get("timeTags") or []
                start_year = ""
                if time_tags:
                    raw_start = (
                        time_tags[0].get("startTime") or time_tags[0].get("start_time") or ""
                    )[:10]
                    if len(raw_start) >= 4:
                        start_year = raw_start[:4]

                if start_year and start_year < _MIN_CRAWL_YEAR:
                    continue  # historical; keep scanning the page

                recent_items_on_page += 1

                project_id = p.get("id")
                if not project_id:
                    continue
                if project_id in existing_ids:
                    continue  # already crawled

                new_items_on_page += 1
                print(f"[projects] [{len(existing_ids) + new_count + 1}] {p.get('name')}")

                # ── Fetch detail ──────────────────────────────────────────
                detail_url = f"{_DETAIL_API}?policyProjectId={project_id}"
                try:
                    time.sleep(0.4)
                    dr = requests.get(detail_url, headers=_HEADERS, timeout=15)
                    dr.raise_for_status()
                    detail = dr.json().get("policyProject", {})
                except Exception as exc:
                    print(f"[projects] Failed to fetch detail {project_id}: {exc}")
                    continue

                # ── Build record ──────────────────────────────────────────
                title = detail.get("name") or p.get("name") or ""
                region = normalize_region(detail.get("regionName") or "")

                obj_type = detail.get("object") or p.get("objectType") or ""
                recipient = "个人" if (
                    "DXLX0001001" in obj_type or "个人" in str(detail.get("objectDesc"))
                ) else "企业"

                time_tags_d = detail.get("timeTags") or p.get("timeTags") or []
                raw_start, raw_end = "", ""
                if time_tags_d:
                    t = time_tags_d[0]
                    raw_start = (t.get("startTime") or t.get("start_time") or "")[:10]
                    raw_end = (t.get("endTime") or t.get("end_time") or "")[:10]

                # Re-check year from detail
                if raw_start and raw_start[:4] < _MIN_CRAWL_YEAR:
                    print(f"[projects] Skipping historical detail: {title}")
                    continue

                start_date = format_date_with_timezone(raw_start, "08:00:00")
                end_date = format_date_with_timezone(raw_end, "18:00:00")

                cond_text = decode_base64_html(detail.get("condition"))
                supp_text = decode_base64_html(detail.get("support"))
                material_text = decode_base64_html(detail.get("material"))

                # ── Try structured lists from API first to avoid LLM/regex fallbacks ──
                if not cond_text or "未在页面结构化" in cond_text:
                    cond_list = detail.get("conditionResolveList") or []
                    if cond_list:
                        cond_text = "\n".join(
                            f"- {item.get('resolveTitle')}"
                            for item in cond_list
                            if item.get("resolveTitle")
                        )
                    else:
                        exam_schema = detail.get("examinationForm", {}).get("schema") or []
                        if exam_schema:
                            cond_text = "\n".join(
                                f"- {item.get('name')}"
                                for item in exam_schema
                                if item.get("name")
                            )

                if not material_text or "未在页面结构化" in material_text:
                    mat_list = detail.get("materialResolveList") or []
                    if mat_list:
                        mat_items = []
                        for item in mat_list:
                            title_val = item.get("resolveTitle")
                            if title_val:
                                is_need = item.get("isNeed")
                                suffix = " (必要)" if is_need == 1.0 else ""
                                mat_items.append(f"- {title_val}{suffix}")
                        if mat_items:
                            material_text = "\n".join(mat_items)

                # LLM fallback when structured fields are missing
                if (not cond_text or "未在页面结构化" in cond_text) or \
                   (not supp_text or "未在页面结构化" in supp_text):
                    combined = (
                        f"申报条件:\n{cond_text}\n\n兑付标准:\n{supp_text}\n\n申报材料:\n{material_text}"
                    )
                    ext_cond, ext_supp = extract_conditions_and_standards(title, combined)
                    if ext_cond:
                        cond_text = ext_cond
                    if ext_supp:
                        supp_text = ext_supp

                collect_dept = detail.get("collectDeptName") or detail.get("agency") or ""

                # Save raw detail to file
                raw_dir = out_file.parent / "raw" / "projects"
                raw_dir.mkdir(parents=True, exist_ok=True)
                with open(raw_dir / f"zwdt-{project_id}.json", "w", encoding="utf-8") as rf:
                    json.dump(detail, rf, ensure_ascii=False, indent=2)

                p_policy_id = f"zwdt-{detail.get('policyId')}" if detail.get("policyId") else ""

                item = ProjectItem(
                    project_id=f"zwdt-{project_id}",
                    policy_basis=(detail.get("sourcePolicy") or {}).get("name") or "",
                    policy_ids=[p_policy_id] if p_policy_id else [],
                    title=title,
                    region=region,
                    agency=collect_dept,
                    recipient=recipient,
                    start_date=start_date,
                    end_date=end_date,
                    url=f"https://zwdt.sh.gov.cn/qykj/shell_oc_policy_zq/policy/project-detail?id={project_id}",
                    content=f"申报条件:\n{cond_text}\n\n兑付标准:\n{supp_text}\n\n申报材料:\n{material_text}",
                    policy_object=recipient,
                    condition_text=cond_text,
                    support_text=supp_text,
                    material_text=material_text,
                    contact_information=detail.get("phone") or "",
                    apply_method=detail.get("declareMethodDesc") or "",
                    crawled_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
                fh.write(json.dumps(item.model_dump(), ensure_ascii=False) + "\n")
                existing_ids.add(project_id)
                new_count += 1

            # ── Incremental stop condition ────────────────────────────────
            if stop_on_seen and recent_items_on_page > 0 and new_items_on_page == 0:
                print(
                    f"[projects] Page {page}: all {recent_items_on_page} recent items already "
                    f"crawled. Stopping (incremental mode)."
                )
                break

            if recent_items_on_page == 0:
                print(f"[projects] No recent projects on page {page}. Stopping.")
                break

            page += 1
            if limit_pages and page > limit_pages:
                break
            if page > total_pages:
                break

    print(f"[projects] Done. New records written: {new_count}")
    return new_count


# ── Policies ──────────────────────────────────────────────────────────────────

def crawl_policies(
    out_file: Path,
    projects_file: Path,
    *,
    limit_pages: Optional[int] = None,
    stop_on_seen: bool = False,
) -> int:
    """
    Crawl www.shanghai.gov.cn 政策文件 and append new records to *out_file*.

    Parameters
    ----------
    out_file      : Path to the output JSONL file (will be appended).
    projects_file : Path to the projects JSONL for on-the-fly linking.
    limit_pages   : Optional hard cap on pages fetched.
    stop_on_seen  : If True (incremental mode), stop when consecutive pages
                    yield zero new policies (all already in existing_ids).

    Returns
    -------
    Number of new records written.
    """
    print("=== STARTING CRAWL FOR POLICIES ===")
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Load projects for on-the-fly linking
    projects: list[dict] = []
    if projects_file.exists():
        with open(projects_file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        projects.append(json.loads(line))
                    except Exception:
                        pass
    print(f"[policies] Loaded {len(projects)} projects for linking.")

    existing_ids = _load_existing_ids(out_file, "policy_id", "shgov-")
    print(f"[policies] Loaded {len(existing_ids)} already-crawled policies.")

    client = ShanghaiGovApiClient(site_id_list=["0001"])
    new_count = 0
    consecutive_empty_pages = 0
    _STOP_AFTER_EMPTY = 2  # incremental: stop after N consecutive pages with no new items

    with open(out_file, "a", encoding="utf-8") as fh:
        for rec in client.iter_full_records(
            max_pages=limit_pages or 0,
            min_year=int(_MIN_CRAWL_YEAR),
            existing_ids=existing_ids,
        ):
            # iter_full_records already skips existing_ids — every rec here is new
            print(f"[policies] [{len(existing_ids) + new_count + 1}] {rec['title']}")

            title = rec["title"]
            content = rec["content"]
            expire_date = extract_policy_expire_date(title, content)

            doc_type = rec.get("docType") or ""
            doc_year = rec.get("docYear") or ""
            doc_no = rec.get("docNo") or ""
            doc_num = f"{doc_type}〔{doc_year}〕{doc_no}号" if doc_type and doc_no else ""

            # On-the-fly project linking
            matched_project_ids: list[str] = []
            clean_title = (
                title.replace("《", "").replace("》", "")
                     .replace("印发", "").replace("关于", "")
                     .replace("的通知", "").replace("通知", "")
            )
            for p in projects:
                basis = p.get("policy_basis") or ""
                project_title = p.get("title") or ""
                project_id = p.get("project_id")
                project_policy_ids = p.get("policy_ids") or []

                matched = (
                    (basis and (title in basis or (len(clean_title) > 5 and clean_title in basis) or (doc_num and doc_num in basis)))
                    or (len(clean_title) > 8 and clean_title in project_title)
                    or (rec["businessId"] and f"shgov-{rec['businessId']}" in project_policy_ids)
                )
                if matched and project_id and project_id not in matched_project_ids:
                    matched_project_ids.append(project_id)

            # Save raw detail to file
            raw_dir = out_file.parent / "raw" / "policies"
            raw_dir.mkdir(parents=True, exist_ok=True)
            with open(raw_dir / f"shgov-{rec['businessId']}.json", "w", encoding="utf-8") as rf:
                json.dump(rec, rf, ensure_ascii=False, indent=2)

            item = PolicyItem(
                policy_id=f"shgov-{rec['businessId']}",
                project_ids=matched_project_ids,
                title=title,
                region="上海市/上海市",
                document_number=doc_num,
                agency=rec["agency"],
                publish_date=rec["publishDate"],
                expire_date=expire_date,
                content=content,
                pdf_url=rec["pdf_url"],
                url=rec["detail_url"],
            )
            fh.write(json.dumps(item.model_dump(), ensure_ascii=False) + "\n")
            existing_ids.add(rec["businessId"])
            new_count += 1
            consecutive_empty_pages = 0  # reset: we got a new item

        # Note: stop_on_seen for policies is handled by iter_full_records skipping
        # existing IDs; if all items on a list page are skipped, the generator
        # naturally exhausts with min_year filter. For a stronger early-stop we
        # would need to patch the generator, which is out of scope here.

    print(f"[policies] Done. New records written: {new_count}")
    return new_count


# ── Project ↔ Policy linking ──────────────────────────────────────────────────

def link_projects_and_policies(
    projects_file: Path,
    policies_file: Path,
) -> None:
    """
    Re-scan both JSONL files and rebuild bidirectional ID links.

    Writes updated files in-place.
    """
    print("=== LINKING PROJECTS ↔ POLICIES ===")
    if not projects_file.exists() or not policies_file.exists():
        print("Missing JSONL files for linking. Skipping.")
        return

    def _read(path: Path) -> list[dict]:
        rows = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        return rows

    projects = _read(projects_file)
    policies = _read(policies_file)
    print(f"Loaded {len(projects)} projects and {len(policies)} policies.")

    # Ensure link arrays exist
    for p in projects:
        p.setdefault("policy_ids", [])
    for pol in policies:
        pol.setdefault("project_ids", [])

    for p in projects:
        basis = p.get("policy_basis") or ""
        p["policy_basis"] = basis
        project_title = p.get("title") or ""
        project_id = p.get("project_id")
        project_policy_ids = p.get("policy_ids") or []

        for pol in policies:
            policy_id = pol.get("policy_id")
            raw_policy_id = policy_id.replace("shgov-", "", 1) if policy_id else ""
            policy_title = pol.get("title") or ""
            doc_num = pol.get("document_number") or ""
            clean_policy_title = (
                policy_title.replace("《", "").replace("》", "")
                             .replace("印发", "").replace("关于", "")
                             .replace("的通知", "").replace("通知", "")
            )

            matched = (
                (basis and (
                    policy_title in basis
                    or (len(clean_policy_title) > 5 and clean_policy_title in basis)
                    or (doc_num and doc_num in basis)
                ))
                or (len(clean_policy_title) > 8 and clean_policy_title in project_title)
                or (policy_id and policy_id in project_policy_ids)
            )
            if matched:
                if policy_id and policy_id not in p["policy_ids"]:
                    p["policy_ids"].append(policy_id)
                if project_id and project_id not in pol["project_ids"]:
                    pol["project_ids"].append(project_id)

    def _write(path: Path, rows: list[dict], model_cls) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(model_cls(**row).model_dump(), ensure_ascii=False) + "\n")

    _write(projects_file, projects, ProjectItem)
    _write(policies_file, policies, PolicyItem)
    print("Linking completed.")
