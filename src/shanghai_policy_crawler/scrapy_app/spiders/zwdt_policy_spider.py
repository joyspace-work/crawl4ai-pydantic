from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import scrapy
from bs4 import BeautifulSoup

from shanghai_policy_crawler.utils import compact_dict, utc_now_iso

PolicyItem = dict[str, Any]


logger = logging.getLogger(__name__)


class ZwdtPolicySpider(scrapy.Spider):
    """上海“一网通办/随申兑”惠企政策申报项目采集器。

    This source is strong for application project coverage and status tracking.
    It is not treated as a government-original policy source; official policy
    originals are kept as related policy metadata when the API exposes them.
    """

    name = "zwdt_policy"
    allowed_domains = ["zwdt.sh.gov.cn"]

    base_url = "https://zwdt.sh.gov.cn"
    shell_url = (
        f"{base_url}/qykj/shell_oc_policy_zq/policy/policyDeclare?"
        "tab=policyCenter&selectParams=%7B%22policyDeclare%22%3A%7B%22policyType%22%3A%5B%5D%7D%7D"
    )
    projects_url = f"{base_url}/qykj/shspace/policy_center/hqPolicy/projects"
    detail_url = f"{base_url}/qykj/shspace/policy_center/hqPolicy/questions"

    policy_types = ["BTLX", "RZLX", "JMLX", "RCLX", "RYLX", "JYLX", "QT"]
    apply_state_labels = {
        "0": "已截止/不可申报",
        "1": "即将开始",
        "2": "申报中",
    }

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 0.4,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 3,
        "OUTPUT_FILE": "output/zwdt_shanghai_policy_projects.jsonl",
        "SUMMARY_OUTPUT_PREFIX": "output/zwdt_policy_project_summary",
    }

    def __init__(
        self,
        keyword: str | None = None,
        max_pages: str | int | None = None,
        page_size: str | int | None = None,
        apply_state: str | None = None,
        region_codes: str | None = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.keyword = keyword or ""
        self.max_pages = int(max_pages) if max_pages not in (None, "") else 0
        self.page_size = int(page_size) if page_size not in (None, "") else 50
        # Empty means all statuses. The public page's default "1,2" omits archived projects.
        self.apply_state = "" if apply_state is None else apply_state
        self.region_codes = [code.strip() for code in (region_codes or "").split(",") if code.strip()]

    def start_requests(self):
        print("DEBUG: start_requests called!")
        req = self._project_request(page=0)
        print("DEBUG: yielded request:", req)
        yield req

    def parse_projects(self, response):
        payload = self._json(response)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            logger.warning("Unexpected ZWDT project payload: %s body=%s", response.url, response.text[:300])
            return

        page = int(data.get("page") or response.meta.get("page") or 0)
        total_pages = int(data.get("pages") or 0)
        total = int(data.get("total") or 0)
        projects = data.get("list") or []
        logger.info("ZWDT project page=%s/%s total=%s items=%s", page + 1, total_pages, total, len(projects))

        for project in projects:
            if not isinstance(project, dict):
                continue
            project_id = str(project.get("id") or "")
            if not project_id:
                continue
            yield scrapy.Request(
                f"{self.detail_url}?{urlencode({'policyProjectId': project_id})}",
                callback=self.parse_detail,
                headers=self._headers(),
                meta={"list_project": project},
                dont_filter=True,
            )

        if not projects:
            return
        next_page = page + 1
        if self.max_pages and next_page >= self.max_pages:
            return
        if total_pages and next_page >= total_pages:
            return
        yield self._project_request(page=next_page)

    def parse_detail(self, response):
        payload = self._json(response)
        detail = payload.get("policyProject") if isinstance(payload, dict) else None
        if not isinstance(detail, dict):
            logger.warning("Unexpected ZWDT detail payload: %s body=%s", response.url, response.text[:300])
            return
        yield self._build_item(response.meta.get("list_project") or {}, detail)

    def _project_request(self, *, page: int) -> scrapy.Request:
        body = {
            "isNeedSort": False,
            "containProject": True,
            "objectType": 1,
            "sortStrategy": 0,
            "page": page,
            "size": self.page_size,
            "policyType": self.policy_types,
            "projectTypes": [],
            "releaseTimeAfter": "",
            "releaseTimeBefore": "",
            "keyWord": self.keyword,
            "open": True,
            "clientType": "1",
        }
        if self.apply_state:
            body["applyState"] = self.apply_state
        if self.region_codes:
            body["administrativeDivisions"] = self.region_codes
        params = urlencode({"page": page, "size": self.page_size, "isNeedSort": "false"})
        return scrapy.Request(
            f"{self.projects_url}?{params}",
            method="POST",
            body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(content_type=True),
            callback=self.parse_projects,
            meta={"page": page},
            dont_filter=True,
        )

    def _build_item(self, list_project: dict[str, Any], detail: dict[str, Any]) -> PolicyItem:
        project_id = str(detail.get("id") or list_project.get("id") or "")
        policy_id = str(detail.get("policyId") or "")
        title = self._clean(detail.get("name") or list_project.get("name") or "")
        detail_page_url = f"{self.base_url}/qykj/shell_oc_policy_zq/policy/project-detail?{urlencode({'id': project_id})}"
        source_policy = detail.get("sourcePolicy") if isinstance(detail.get("sourcePolicy"), dict) else {}
        related_policy_titles = self._related_policy_titles(detail)
        policy_basis = self._clean(
            source_policy.get("name")
            or detail.get("policyBasis")
            or ("；".join(related_policy_titles) if related_policy_titles else "")
        )
        source = self._clean(
            detail.get("handingDeptName")
            or detail.get("publishDepartmentName")
            or list_project.get("handingDeptName")
            or list_project.get("collectDepartmentName")
            or source_policy.get("publishDepartmentName")
        )
        start_date, end_date = self._main_time_range(detail.get("timeTags") or list_project.get("timeTags") or [])
        publish_date = self._date_only(
            source_policy.get("releaseTime")
            or detail.get("createtime")
            or detail.get("updatetime")
            or start_date
        )
        policy_object = self._clean(detail.get("objectDesc") or self._object_label(detail.get("object")) or "")
        support_text = self._decode_html(detail.get("support"))
        condition_text = self._decode_html(detail.get("condition"))
        material_text = self._clean(detail.get("material") or "")
        apply_method = self._apply_method(detail)
        contact = self._contact_information(detail)
        content = self._compose_content(
            policy_basis=policy_basis,
            policy_object=policy_object,
            apply_method=apply_method,
            support=support_text,
            condition=condition_text,
            material=material_text,
            contact=contact,
            related_policies=related_policy_titles,
        )
        categories = self._category_labels(detail, list_project)
        crawled_at = utc_now_iso()

        item: PolicyItem = {
            "title": title,
            "publish_date": publish_date,
            "source": source,
            "government_original_url": detail_page_url,
            "content": content,
            "url": detail_page_url,
            "attachments": self._attachments(detail),
            "crawled_at": crawled_at,
            "source_url": detail_page_url,
            "content_text": content,
            "content_markdown": content,
            "scraped_at": crawled_at,
            "city": "上海市",
            "district": self._region_name(detail, list_project),
            "source_type": "zwdt_policy_project",
            "source_site": "zwdt.sh.gov.cn",
            "category_labels": categories,
            "category_values": categories,
        }
        item.update(
            compact_dict(
                {
                    "project_id": project_id,
                    "policy_id": policy_id,
                    "policy_basis": policy_basis,
                    "official_original_url": "",
                    "application_portal_url": detail.get("immediateDeclarationLink") or list_project.get("immediateDeclarationLink") or "",
                    "application_start_date": start_date,
                    "application_end_date": end_date,
                    "application_period": f"{start_date} 至 {end_date}" if start_date or end_date else "",
                    "apply_state": detail.get("applyState") or list_project.get("applyState"),
                    "apply_state_label": self._apply_state_label(detail.get("applyState") or list_project.get("applyState")),
                    "free_enjoy": detail.get("freeEnjoy") if detail.get("freeEnjoy") is not None else list_project.get("freeEnjoy"),
                    "redemption_type": detail.get("redemptionType") or list_project.get("redemptionType"),
                    "policy_level": detail.get("policyLevel") or list_project.get("policyLevel"),
                    "region_code": detail.get("regions") or list_project.get("regions"),
                    "handing_department_code": detail.get("handingDept") or list_project.get("handingDept"),
                    "publish_department_code": detail.get("publishDepartment"),
                    "policy_types": detail.get("projectTypes") or list_project.get("policyType"),
                    "support_text": support_text,
                    "condition_text": condition_text,
                    "material_text": material_text,
                    "apply_method": apply_method,
                    "contact_information": contact,
                    "related_policies": detail.get("relatedPolicies") or [],
                    "source_policy": source_policy,
                    "raw_list_project": list_project,
                    "raw_detail": detail,
                    "政策依据": policy_basis,
                    "政策对象": policy_object,
                    "政策条件": condition_text,
                    "兑付标准": support_text,
                    "申报材料": material_text,
                    "申报方式": apply_method,
                    "联系方式": contact,
                    "发布时间": publish_date,
                    "申报期限": f"{start_date} 至 {end_date}" if start_date or end_date else "",
                    "第三张图类别": {
                        "行政区划": self._region_name(detail, list_project),
                        "受理部门": source,
                        "政策类型": categories,
                        "申报状态": self._apply_state_label(detail.get("applyState") or list_project.get("applyState")),
                        "是否免申": bool(detail.get("freeEnjoy") if detail.get("freeEnjoy") is not None else list_project.get("freeEnjoy")),
                    },
                }
            )
        )
        return item

    def _headers(self, *, content_type: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": self.shell_url,
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            ),
        }
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _json(self, response) -> dict[str, Any]:
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            return {}

    def _decode_html(self, value: Any) -> str:
        if not value:
            return ""
        raw = str(value)
        try:
            raw = base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:
            pass
        return self._html_to_text(raw)

    def _html_to_text(self, text: str) -> str:
        soup = BeautifulSoup(text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n"), "html.parser")
        for tag in soup.select("script,style,noscript,svg"):
            tag.decompose()
        return self._clean(soup.get_text("\n"))

    def _compose_content(
        self,
        *,
        policy_basis: str,
        policy_object: str,
        apply_method: str,
        support: str,
        condition: str,
        material: str,
        contact: str,
        related_policies: list[str],
    ) -> str:
        sections = [
            ("政策依据", policy_basis),
            ("申报对象", policy_object),
            ("申报方式", apply_method),
            ("支持标准/方式", support),
            ("申报条件", condition),
            ("申报材料", material),
            ("联系方式", contact),
            ("相关政策", "\n".join(related_policies)),
        ]
        return "\n\n".join(f"{title}\n{value}" for title, value in sections if value)

    def _apply_method(self, detail: dict[str, Any]) -> str:
        values = []
        if detail.get("declareMethodDesc"):
            values.append(str(detail["declareMethodDesc"]))
        if detail.get("handleAddress"):
            values.append(f"线下办理地址：{detail['handleAddress']}")
        if detail.get("immediateDeclarationLink"):
            values.append(f"线上申报地址：{detail['immediateDeclarationLink']}")
        return self._clean("\n".join(values))

    def _contact_information(self, detail: dict[str, Any]) -> str:
        parts = []
        if detail.get("phone"):
            parts.append(f"联系电话：{detail['phone']}")
        if detail.get("superviseTel"):
            parts.append(f"监督电话：{detail['superviseTel']}")
        for department in detail.get("implementationDepartment") or []:
            if isinstance(department, dict):
                text = " ".join(str(department.get(key) or "") for key in ("deptName", "address", "phone"))
                if text.strip():
                    parts.append(text)
        return self._clean("\n".join(parts))

    def _category_labels(self, detail: dict[str, Any], list_project: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for entry in detail.get("classification") or []:
            if isinstance(entry, dict) and entry.get("name"):
                values.append(str(entry["name"]))
            elif isinstance(entry, str):
                values.append(entry)
        for entry in detail.get("category") or []:
            if isinstance(entry, dict) and entry.get("name"):
                values.append(str(entry["name"]))
        values.extend(str(value) for value in (list_project.get("policyType") or []) if value)
        if detail.get("redemptionType") or list_project.get("redemptionType"):
            values.append(str(detail.get("redemptionType") or list_project.get("redemptionType")))
        return list(dict.fromkeys(self._clean(value) for value in values if self._clean(value)))

    def _attachments(self, detail: dict[str, Any]) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        for key in ("files", "downloadUrl", "guideUrl", "flowPic"):
            value = detail.get(key)
            if isinstance(value, str) and value.startswith("http"):
                attachments.append({"name": key, "url": value})
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        url = entry.get("url") or entry.get("fileUrl") or entry.get("downloadUrl")
                        if url:
                            attachments.append({"name": self._clean(entry.get("name") or entry.get("fileName") or key), "url": str(url)})
        return attachments

    def _related_policy_titles(self, detail: dict[str, Any]) -> list[str]:
        titles: list[str] = []
        source_policy = detail.get("sourcePolicy")
        if isinstance(source_policy, dict) and source_policy.get("name"):
            titles.append(self._clean(source_policy["name"]))
        for key in ("relatedPolicies", "policyOriginal"):
            for policy in detail.get(key) or []:
                if isinstance(policy, dict) and policy.get("name"):
                    titles.append(self._clean(policy["name"]))
        return list(dict.fromkeys(title for title in titles if title))

    def _main_time_range(self, tags: list[Any]) -> tuple[str, str]:
        if not isinstance(tags, list) or not tags:
            return "", ""
        selected = None
        for tag in tags:
            if isinstance(tag, dict) and tag.get("main"):
                selected = tag
                break
        if selected is None:
            selected = tags[0] if isinstance(tags[0], dict) else {}
        return (
            self._date_only(selected.get("startTime") or selected.get("start_time")),
            self._date_only(selected.get("endTime") or selected.get("end_time")),
        )

    def _region_name(self, detail: dict[str, Any], list_project: dict[str, Any]) -> str:
        region = self._clean(detail.get("regionName") or "")
        if region:
            return region
        for value in (detail.get("regions"), list_project.get("regions")):
            text = self._clean(value)
            if text and not re.fullmatch(r"[A-Z0-9]+", text):
                return text
        collect_department = self._clean(list_project.get("collectDepartmentName") or "")
        if collect_department and not re.search(r"(委员会|管理局|监督局|发展改革委|商务委|科委|经信委|财政局|部门)$", collect_department):
            return collect_department
        for value in (detail.get("regions"), list_project.get("regions")):
            text = self._clean(value)
            if text:
                return text
        return collect_department

    def _apply_state_label(self, value: Any) -> str:
        key = str(int(value)) if isinstance(value, (int, float)) else str(value or "")
        return self.apply_state_labels.get(key, key)

    def _object_label(self, value: Any) -> str:
        mapping = {
            "DXLX0001001": "个人",
            "DXLX0001002": "企业",
        }
        return mapping.get(str(value or ""), "")

    def _date_only(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        match = re.search(r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}", text)
        if match:
            return match.group(0).replace("年", "-").replace("月", "-").replace("/", "-").rstrip("日")
        try:
            return datetime.fromisoformat(text.replace("+0800", "+08:00")).date().isoformat()
        except ValueError:
            return text[:10]

    def _clean(self, value: Any) -> str:
        text = str(value or "").replace("\xa0", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
