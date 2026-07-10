import os
import sys
import json
import time
import base64
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urlencode

# Ensure python path includes src
sys.path.insert(0, str(Path(__file__).parent / "src"))

from shanghai_policy_crawler.shanghai_gov_api import ShanghaiGovApiClient

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class ProjectItem(BaseModel):
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

# Output goes to website project (crawl4ai app), not here
OUTPUT_DIR = Path("/Users/a12/projects/website/apps/crawl4ai/output")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_KEY") or ""

def decode_base64_html(val):
    if not val:
        return ""
    try:
        decoded = base64.b64decode(str(val)).decode("utf-8", errors="replace")
        soup = BeautifulSoup(decoded.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n"), "html.parser")
        return soup.get_text("\n").strip()
    except Exception:
        return str(val).strip()

def normalize_region(region_text, collect_dept_name):
    combined = f"{region_text or ''} {collect_dept_name or ''}"
    districts = [
        ("闵行", "上海市/上海市/闵行区"),
        ("浦东", "上海市/上海市/浦东新区"),
        ("黄浦", "上海市/上海市/黄浦区"),
        ("徐汇", "上海市/上海市/徐汇区"),
        ("长宁", "上海市/上海市/长宁区"),
        ("静安", "上海市/上海市/静安区"),
        ("普陀", "上海市/上海市/普陀区"),
        ("虹口", "上海市/上海市/虹口区"),
        ("杨浦", "上海市/上海市/杨浦区"),
        ("宝山", "上海市/上海市/宝山区"),
        ("嘉定", "上海市/上海市/嘉定区"),
        ("金山", "上海市/上海市/金山区"),
        ("松江", "上海市/上海市/松江区"),
        ("青浦", "上海市/上海市/青浦区"),
        ("奉贤", "上海市/上海市/奉贤区"),
        ("崇明", "上海市/上海市/崇明区"),
        ("临港", "上海市/上海市/浦东新区"),
    ]
    for key, val in districts:
        if key in combined:
            return val
    if "上海" in combined:
        return "上海市/上海市"
    return "全国"

def format_date_with_timezone(date_str, default_time="08:00:00"):
    """Format a partial date string (e.g. 2026-05-20) into YYYY-MM-DD HH:mm:ss GMT+8"""
    if not date_str:
        return None
    cleaned = date_str.strip()
    if len(cleaned) >= 10:
        base_date = cleaned[:10]
        # Check if it already contains time
        if len(cleaned) >= 19:
            time_part = cleaned[11:19]
            return f"{base_date} {time_part} GMT+8"
        return f"{base_date} {default_time} GMT+8"
    return cleaned

def call_openrouter_llm(prompt: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://joyspace.work",
        "X-Title": "JoySpace Crawler"
    }
    payload = {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4000
    }
    for attempt in range(8):
        try:
            # Respect rate limit by introducing a larger sleep before call
            time.sleep(3.0)
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            res = resp.json()
            content = res["choices"][0]["message"].get("content")
            return content.strip() if content else ""
        except Exception as e:
            backoff = (attempt + 1) * 10
            print(f"LLM Call error (attempt {attempt+1}): {e}. Retrying in {backoff}s...")
            time.sleep(backoff)
    return ""

def extract_policy_expire_date(title, content):
    """Ask LLM to determine the expiration date (expire_date) from policy content."""
    prompt = f"""你是一个专业的政策文本分析助手。请阅读以下政策文件，找出它的失效日期/废止日期/有效期截止日期。
政策标题: {title}
政策正文片段:
{content[:3000]}

请仔细寻找正文里如“本办法自XXXX年XX月XX日起施行，有效期至XXXX年XX月XX日”或“有效期X年”的内容。
如果找到明确的失效日期，请将其规整为 YYYY-MM-DD 格式输出。
如果只说有效期几年，请根据发文时间或施行时间计算出失效日期，规整为 YYYY-MM-DD 输出。
如果政策中没有任何关于有效期或失效日期的描述，请直接输出 "null"。
不要有任何多余的话，直接输出日期（如 2031-12-31）或者 "null"。"""
    
    output = call_openrouter_llm(prompt).strip()
    # Normalize output
    import re
    date_match = re.search(r"20\d{2}-\d{2}-\d{2}", output)
    if date_match:
        return date_match.group(0)
    return None

def extract_conditions_and_standards_with_llm(title, raw_text):
    truncated_text = (raw_text or "")[:10000]
    prompt = f"""你是一个专业的政策申报数据提取助手。请根据以下项目名称和正文内容，提取出该项目的“申报条件”和“兑付标准/支持标准”。
项目名称: {title}
正文内容:
{truncated_text}

请务必按以下JSON格式输出，不要包含任何额外的Markdown格式标记（如```json）、推理过程或额外文字，直接输出合法的JSON对象：
{{
  "policy_conditions": "这里写详细的申报条件...",
  "payment_standard": "这里写详细的兑付/补贴标准，如果有明确的资金补贴数额也写在这里..."
}}
"""
    llm_output = call_openrouter_llm(prompt)
    if "```" in llm_output:
        llm_output = llm_output.split("```")
        for chunk in llm_output:
            if chunk.strip().startswith("{") or chunk.strip().startswith("json\n{"):
                llm_output = chunk.replace("json\n", "", 1).strip()
                break
    try:
        parsed = json.loads(llm_output)
        return parsed.get("policy_conditions", ""), parsed.get("payment_standard", "")
    except Exception:
        import re
        cond = ""
        std = ""
        # Try to find "policy_conditions": "..." or "policy_conditions":"..."
        cond_match = re.search(r"\"policy_conditions\"\s*:\s*\"(.*?)\"(,|\s*\})", llm_output, re.DOTALL)
        if cond_match:
            cond = cond_match.group(1).strip()
        else:
            cond_trunc_match = re.search(r"\"policy_conditions\"\s*:\s*\"(.*)", llm_output, re.DOTALL)
            if cond_trunc_match:
                cond = cond_trunc_match.group(1).strip()
                if "\"payment_standard\"" in cond:
                    cond = cond.split("\"payment_standard\"")[0].strip().rstrip(",").rstrip("\"").strip()
        
        std_match = re.search(r"\"payment_standard\"\s*:\s*\"(.*?)\"(,|\s*\})", llm_output, re.DOTALL)
        if std_match:
            std = std_match.group(1).strip()
        else:
            std_trunc_match = re.search(r"\"payment_standard\"\s*:\s*\"(.*)", llm_output, re.DOTALL)
            if std_trunc_match:
                std = std_trunc_match.group(1).strip().rstrip("}").rstrip("\"").strip()
                
        if cond or std:
            print(f"Extracted partially using regex fallback for: {title}")
            return cond.replace("\\n", "\n").replace('\\"', '"'), std.replace("\\n", "\n").replace('\\"', '"')
            
        print("Failed to parse JSON from LLM output:", llm_output[:300])
        return "", ""

def crawl_projects(limit_pages=None):
    print("=== STARTING CRAWL FOR PROJECTS ===")
    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "projects.jsonl"
    
    projects_url = "https://zwdt.sh.gov.cn/qykj/shspace/policy_center/hqPolicy/projects"
    detail_base_url = "https://zwdt.sh.gov.cn/qykj/shspace/policy_center/hqPolicy/questions"
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://zwdt.sh.gov.cn/qykj/shell_oc_policy_zq/policy/policyDeclare",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    }
    existing_ids = set()
    if out_file.exists():
        with open(out_file, "r", encoding="utf-8") as f_in:
            for line in f_in:
                if line.strip():
                    try:
                        data = json.loads(line)
                        if data.get("project_id") and data["project_id"].startswith("zwdt-"):
                            existing_ids.add(data["project_id"].replace("zwdt-", "", 1))
                    except Exception:
                        pass
    print(f"Loaded {len(existing_ids)} already crawled projects from file.")
    
    page = 1
    page_size = 50
    scraped_count = len(existing_ids)
    
    with open(out_file, "a", encoding="utf-8") as f_out:
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
                url = f"{projects_url}?page={page}&size={page_size}"
                r = requests.post(url, headers=headers, json=body, timeout=20)
                r.raise_for_status()
                res = r.json()
            except Exception as e:
                print(f"Failed to fetch page {page}: {e}")
                break
                
            data = res.get("data") or {}
            projects_list = data.get("list", [])
            total_pages = data.get("pages", 1)
            
            if not projects_list:
                break
                
            page_has_recent = False
            for p in projects_list:
                # 2025/2026 Year Filter
                time_tags = p.get("timeTags") or []
                start_year = ""
                if time_tags:
                    t = time_tags[0]
                    raw_start = (t.get("startTime") or t.get("start_time") or "")[:10]
                    if len(raw_start) >= 4:
                        start_year = raw_start[:4]
                
                # Skip detail fetch for historical projects
                if start_year and start_year < "2025":
                    continue
                
                page_has_recent = True
                
                project_id = p.get("id")
                if not project_id:
                    continue
                if project_id in existing_ids:
                    continue
                
                print(f"[{scraped_count + 1}] Project: {p.get('name')}")
                
                # Fetch details
                detail_url = f"{detail_base_url}?policyProjectId={project_id}"
                try:
                    time.sleep(0.4)
                    dr = requests.get(detail_url, headers=headers, timeout=15)
                    dr.raise_for_status()
                    detail_data = dr.json().get("policyProject", {})
                except Exception as e:
                    print(f"Failed to fetch detail for {project_id}: {e}")
                    continue
                
                # Title
                title = detail_data.get("name") or p.get("name") or ""
                
                # Region (Normalized region formatting)
                collect_dept_name = p.get("collectDepartmentName") or detail_data.get("handingDeptName") or ""
                region_name = detail_data.get("regionName") or ""
                region = normalize_region(region_name, collect_dept_name)
                
                # Recipient (申报对象)
                obj_type = detail_data.get("object") or p.get("objectType") or ""
                recipient = "企业"
                if "DXLX0001001" in obj_type or "个人" in str(detail_data.get("objectDesc")):
                    recipient = "个人"
                
                # Dates and Timezones formatting
                time_tags = detail_data.get("timeTags") or p.get("timeTags") or []
                raw_start, raw_end = "", ""
                if time_tags:
                    t = time_tags[0]
                    raw_start = (t.get("startTime") or t.get("start_time") or "")[:10]
                    raw_end = (t.get("endTime") or t.get("end_time") or "")[:10]
                
                # Check year again in case list-level timeTags was missing/empty
                detail_start_year = ""
                if len(raw_start) >= 4:
                    detail_start_year = raw_start[:4]
                if detail_start_year and detail_start_year < "2025":
                    print(f"Skipping historical detail: {title} (Year: {detail_start_year})")
                    continue
                
                start_date = format_date_with_timezone(raw_start, "08:00:00")
                end_date = format_date_with_timezone(raw_end, "18:00:00")
                
                # Decoded condition/support texts
                cond_text = decode_base64_html(detail_data.get("condition"))
                supp_text = decode_base64_html(detail_data.get("support"))
                material_text = decode_base64_html(detail_data.get("material"))
                
                # LLM Fallback if fields are empty
                is_cond_empty = not cond_text or "未在页面结构化" in cond_text
                is_supp_empty = not supp_text or "未在页面结构化" in supp_text
                if (is_cond_empty or is_supp_empty) and (cond_text or supp_text or material_text):
                    combined_body = f"申报条件:\n{cond_text}\n\n兑付标准:\n{supp_text}\n\n申报材料:\n{material_text}"
                    ext_cond, ext_supp = extract_conditions_and_standards_with_llm(title, combined_body)
                    if ext_cond:
                        cond_text = ext_cond
                    if ext_supp:
                        supp_text = ext_supp
                item_data = ProjectItem(
                    project_id=f"zwdt-{project_id}",
                    policy_id=f"shgov-{detail_data.get('policyId')}" if detail_data.get("policyId") else "",
                    policy_basis=(detail_data.get("sourcePolicy") or {}).get("name") or "",
                    policy_ids=[],
                    title=title,
                    region=region,
                    agency=collect_dept_name,
                    recipient=recipient,
                    start_date=start_date,
                    end_date=end_date,
                    url=f"https://zwdt.sh.gov.cn/qykj/shell_oc_policy_zq/policy/project-detail?id={project_id}",
                    content=f"申报条件:\n{cond_text}\n\n兑付标准:\n{supp_text}\n\n申报材料:\n{material_text}",
                    policy_object=recipient,
                    condition_text=cond_text,
                    support_text=supp_text,
                    material_text=material_text,
                    contact_information=detail_data.get("phone") or "",
                    apply_method=detail_data.get("declareMethodDesc") or "",
                    crawled_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    raw_detail=detail_data
                )
                
                f_out.write(json.dumps(item_data.model_dump(), ensure_ascii=False) + "\n")
                scraped_count += 1
                
            if not page_has_recent:
                print("No recent projects found on this page. Stopping crawl.")
                break
                
            page += 1
            if limit_pages and page >= limit_pages:
                break
            if page >= total_pages:
                break
                
    print(f"Crawl completed. Total projects: {scraped_count}")

def crawl_policies(limit_pages=5):
    print("=== STARTING CRAWL FOR POLICIES ===")
    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "policies.jsonl"
    
    # Load all crawled projects to link on the fly
    projects = []
    projects_file = out_dir / "projects.jsonl"
    if projects_file.exists():
        with open(projects_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        projects.append(json.loads(line))
                    except Exception:
                        pass
    print(f"Loaded {len(projects)} projects for on-the-fly linking during policy crawl.")
    
    existing_ids = set()
    if out_file.exists():
        with open(out_file, "r", encoding="utf-8") as f_in:
            for line in f_in:
                if line.strip():
                    try:
                        data = json.loads(line)
                        if data.get("policy_id") and data["policy_id"].startswith("shgov-"):
                            existing_ids.add(data["policy_id"].replace("shgov-", "", 1))
                    except Exception:
                        pass
    print(f"Loaded {len(existing_ids)} already crawled policies from file.")
    
    client = ShanghaiGovApiClient(site_id_list=["0001"])
    count = len(existing_ids)
    
    with open(out_file, "a", encoding="utf-8") as f_out:
        for rec in client.iter_full_records(max_pages=limit_pages, min_year=2025, existing_ids=existing_ids):
            print(f"[{count+1}] Policy: {rec['title']}")
            
            title = rec["title"]
            content = rec["content"]
            
            # Extract expire_date using LLM
            expire_date = extract_policy_expire_date(title, content)
            
            doc_type = rec.get("docType") or ""
            doc_year = rec.get("docYear") or ""
            doc_no = rec.get("docNo") or ""
            doc_num = f"{doc_type}〔{doc_year}〕{doc_no}号" if doc_type and doc_no else ""

            # On-the-fly linking for the current policy
            matched_project_ids = []
            clean_policy_title = title.replace("《", "").replace("》", "").replace("印发", "").replace("关于", "").replace("的通知", "").replace("通知", "")
            for p in projects:
                basis = p.get("policy_basis") or ""
                if not basis:
                    source_policy = p.get("raw_detail", {}).get("sourcePolicy") or {}
                    basis = source_policy.get("name") or ""
                
                project_title = p.get("title") or ""
                project_id = p.get("project_id")
                
                matched = False
                # 1. Match by basis mentioning policy title or clean title or doc_num
                if basis:
                    if (title in basis) or \
                       (len(clean_policy_title) > 5 and clean_policy_title in basis) or \
                       (doc_num and doc_num in basis):
                        matched = True
                        
                # 2. Match by project title containing clean policy title
                if not matched and len(clean_policy_title) > 8 and clean_policy_title in project_title:
                    matched = True
                    
                # 3. Match by direct policyId returned in project raw_detail matching policy businessId
                raw_detail_policy_id = p.get("raw_detail", {}).get("policyId") or ""
                if not matched and raw_detail_policy_id and raw_detail_policy_id == rec["businessId"]:
                    matched = True
                    
                if matched and project_id:
                    if project_id not in matched_project_ids:
                        matched_project_ids.append(project_id)

            item_data = PolicyItem(
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
                raw_detail=rec
            )
            f_out.write(json.dumps(item_data.model_dump(), ensure_ascii=False) + "\n")
            count += 1
            
    print(f"Crawl completed. Total policies: {count}")

def link_projects_and_policies_in_files():
    print("=== LINKING PROJECTS AND POLICIES IN JSONL FILES ===")
    out_dir = OUTPUT_DIR
    projects_file = out_dir / "projects.jsonl"
    policies_file = out_dir / "policies.jsonl"
    
    if not projects_file.exists() or not policies_file.exists():
        print("Missing JSONL files for linking. Skipping.")
        return
        
    # Read projects
    projects = []
    with open(projects_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    projects.append(json.loads(line))
                except Exception:
                    pass
                    
    # Read policies
    policies = []
    with open(policies_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    policies.append(json.loads(line))
                except Exception:
                    pass
                    
    print(f"Loaded {len(projects)} projects and {len(policies)} policies from files.")
    
    # Initialize link arrays and make sure they are present
    for p in projects:
        if "policy_ids" not in p:
            p["policy_ids"] = []
    for pol in policies:
        if "project_ids" not in pol:
            pol["project_ids"] = []
        
    # Matching logic
    for p in projects:
        # Extract basis from project
        basis = p.get("policy_basis") or ""
        if not basis:
            source_policy = p.get("raw_detail", {}).get("sourcePolicy") or {}
            basis = source_policy.get("name") or ""
            p["policy_basis"] = basis
            
        project_title = p.get("title") or ""
        project_id = p.get("project_id")
        
        for pol in policies:
            policy_id = pol.get("policy_id")
            raw_policy_id = policy_id.replace("shgov-", "", 1) if policy_id else ""
            policy_title = pol.get("title") or ""
            doc_num = pol.get("document_number") or "" # e.g. "沪经信规范〔2024〕1号"
            
            # Clean title matching helper
            clean_policy_title = policy_title.replace("《", "").replace("》", "").replace("印发", "").replace("关于", "").replace("的通知", "").replace("通知", "")
            
            matched = False
            
            # 1. Match by basis mentioning policy title or clean title or doc_num
            if basis:
                if (policy_title in basis) or \
                   (len(clean_policy_title) > 5 and clean_policy_title in basis) or \
                   (doc_num and doc_num in basis):
                    matched = True
                    
            # 2. Match by project title containing clean policy title
            if not matched and len(clean_policy_title) > 8 and clean_policy_title in project_title:
                matched = True
                
            # 3. Match by direct policyId returned in project raw_detail matching policy businessId
            raw_detail_policy_id = p.get("raw_detail", {}).get("policyId") or ""
            if not matched and raw_detail_policy_id and raw_detail_policy_id == raw_policy_id:
                matched = True
                
            if matched:
                if policy_id not in p["policy_ids"]:
                    p["policy_ids"].append(policy_id)
                if project_id not in pol["project_ids"]:
                    pol["project_ids"].append(project_id)
                    
    # Write back projects
    with open(projects_file, "w", encoding="utf-8") as f:
        for p in projects:
            validated_p = ProjectItem(**p)
            f.write(json.dumps(validated_p.model_dump(), ensure_ascii=False) + "\n")
            
    # Write back policies
    with open(policies_file, "w", encoding="utf-8") as f:
        for pol in policies:
            validated_pol = PolicyItem(**pol)
            f.write(json.dumps(validated_pol.model_dump(), ensure_ascii=False) + "\n")
            
    print("Linking completed. Files updated.")

if __name__ == "__main__":
    # Pre-link existing files to ensure all historical records are linked
    link_projects_and_policies_in_files()
    
    # Run full crawl (no limits)
    crawl_projects(limit_pages=None)
    crawl_policies(limit_pages=None)
    
    # Finalize bidirectional file linking
    link_projects_and_policies_in_files()

