/**
 * Joyspace AI Search 手动上传脚本
 * 
 * 规范：
 * - joyspace-policy:  region(text) + agency(text) + category(text) + publishdate(datetime) + policy_type(text)
 * - joyspace-project: region(text) + agency(text) + recipient(text) + startdate(datetime) + enddate(datetime)
 * 
 * 元数据设计说明：
 * - agency 仅包含牵头发文部门/受理部门（例如 "上海市闵行区经济委员会"），联合发文的二级部门（如 "上海市闵行区财政局"）只存在 D1 关系型数据库，避免在 AI Search 做不支持的并列模糊匹配。
 * - joyspace-project 的 recipient (申报对象) 用于 Query Filter。
 */

const TOKEN = "cfoat_ZIzCxnPfBJ396nCMSle9smYp8lUPySNXkvBZRRdm-9Q.GF1CrVDyaTlVWGuBJkT6WmBJFj1QjE0CSLW5aqc-Sr4";
const ACCOUNT = "f236185f93c49e07d4a6d364cf82c165";
const NS = "joyspace";

/**
 * 将日期字符串转为 ISO 8601 格式
 * @param {string|null} dateStr - "2026-05-20" 等格式
 * @returns {string|null}
 */
function toISO(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) {
    console.warn(`⚠️  无效日期: ${dateStr}`);
    return null;
  }
  return d.toISOString();
}

/**
 * 上传文档到 AI Search 实例
 * @param {string} instance - 实例名称
 * @param {string} filename - 文件名（作为唯一 key）
 * @param {string} mdContent - Markdown 正文
 * @param {object} meta - 元数据对象
 */
async function upload(instance, filename, mdContent, meta) {
  const url = `https://api.cloudflare.com/client/v4/accounts/${ACCOUNT}/ai-search/namespaces/${NS}/instances/${instance}/items`;
  
  // 过滤 null / undefined 值
  const cleanMeta = Object.fromEntries(
    Object.entries(meta).filter(([, v]) => v !== null && v !== undefined && v !== "")
  );
  
  const form = new FormData();
  form.append("file", new Blob([mdContent], { type: "text/markdown" }), filename);
  form.append("metadata", JSON.stringify(cleanMeta));
  
  const res = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}` },
    body: form,
  });
  const json = await res.json();
  
  if (!json.success) {
    console.error(`❌ ${instance}/${filename} 上传失败:`, JSON.stringify(json.errors));
  } else {
    console.log(`✅ ${instance}/${filename}  id=${json.result?.id}`);
  }
  return json;
}

// ===========================================================
// 上传一个 project 项目
// ===========================================================
async function uploadProject({ filename, title, startdate, enddate, region, agency, recipient, content }) {
  const md = `# ${title}\n\n${content}`;
  const meta = {
    region,
    agency,
    recipient,
    startdate: toISO(startdate),
    enddate:   toISO(enddate),
  };
  return upload("joyspace-project", filename, md, meta);
}

// ===========================================================
// 上传一个 policy 政策原文
// ===========================================================
async function uploadPolicy({ filename, title, publishdate, policyType, region, agency, category, content }) {
  const md = `# ${title}\n\n${content}`;
  const meta = {
    region,
    agency,
    category,
    publishdate: toISO(publishdate),
    policy_type: policyType,
  };
  return upload("joyspace-policy", filename, md, meta);
}

// ===========================================================
// 执行上传
// ===========================================================
async function run() {
  // 1. 上传政策原文
  await uploadPolicy({
    filename:    "policy-zwdt-6810365439808619cf4e1127.md",
    title:       "关于印发《闵行区关于大力推进新型工业化高质量发展的若干政策意见的操作细则》的通知",
    publishdate: "2024-05-08",
    policyType:  "操作细则",
    region:      "上海市/上海市/闵行区",
    // 牵头主发文部门存入元数据，联合部门 (如财政局) 存在 D1
    agency:      "上海市闵行区经济委员会",
    category:    "培育行业领军企业",
    content: `闵行区
闵经委规发〔2024〕1号
发文时间：2024-05-08

各镇人民政府、街道办事处，莘庄工业区管委会，区政府各委、办、局，有关单位：
现将《闵行区关于大力推进新型工业化高质量发展的若干政策意见的操作细则》印发你们，请认真遵照执行。

## 七、培育行业领军企业

### 7.1 支持标准
对当年度首次获评上海市专精特新中小企业、国家级专精特新“小巨人”企业的，分别给予上限25万元和60万元的资助。对通过复评的上海市专精特新中小企业，给予5万元的一次性资助。对首次获评工信部专精特新“小巨人”企业的，给予60万元的一次性资助。

### 7.2 支持标准
对获评工信部制造业单项冠军企业的，给予上限1000万元的资助。`,
  });

  // 2. 上传关联的项目
  await uploadProject({
    filename:  "project-zwdt-68f203cd90a5af686fa6bef6.md",
    title:     "首次获评国家级专精特新小巨人企业资助项目",
    startdate: "2026-05-20",
    enddate:   "2026-06-05",
    region:    "上海市/上海市/闵行区",
    agency:    "上海市闵行区经济委员会",
    recipient: "企业",
    content: `## 基本信息

- 受理部门：闵行区经济委员会
- 申报对象：企业
- 申报时间：2026-05-20 至 2026-06-05
- 申报方式：线上申请
- 联系方式：闵行区经济委员会 企业服务中心 34203361

## 支持标准
对首次获评工信部专精特新“小巨人”企业的，给予60万元的一次性资助。

## 申报条件
获评工信部专精特新“小巨人”的企业，并按时于上海市企业服务云填报运行监测以及年度信息更新。

## 申报材料
本政策采用免申即享方式，无需企业主动提交任何纸质材料。`,
  });
}

run().catch(console.error);
