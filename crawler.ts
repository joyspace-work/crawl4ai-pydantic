import { z } from "zod";

/**
 * Automatically generated from Pydantic models (models.py) via export_schema.py.
 * DO NOT EDIT MANUALLY.
 */

export const crawlerProjectSchema = z.object({ "url": z.string(), "project_id": z.string(), "policy_id": z.string(), "policy_basis": z.string(), "policy_ids": z.array(z.string()).optional(), "title": z.string(), "region": z.string(), "agency": z.string(), "recipient": z.string(), "start_date": z.union([z.string(), z.null()]).default(null), "end_date": z.union([z.string(), z.null()]).default(null), "content": z.string(), "policy_object": z.string(), "condition_text": z.string(), "support_text": z.string(), "material_text": z.string(), "contact_information": z.string(), "apply_method": z.string(), "crawled_at": z.string(), "raw_detail": z.record(z.string(), z.any()) }).describe("申报项目（上海市企业创新港政策申报项目）");
export type CrawlerProject = z.infer<typeof crawlerProjectSchema>;

export const crawlerPolicySchema = z.object({ "url": z.union([z.string(), z.null()]), "policy_id": z.string(), "project_ids": z.array(z.string()).optional(), "title": z.string(), "region": z.string(), "document_number": z.string(), "agency": z.string(), "publish_date": z.union([z.string(), z.null()]).default(null), "expire_date": z.union([z.string(), z.null()]).default(null), "content": z.string(), "pdf_url": z.union([z.string(), z.null()]).default(null), "raw_detail": z.record(z.string(), z.any()) }).describe("政策文件（上海市政府政策）");
export type CrawlerPolicy = z.infer<typeof crawlerPolicySchema>;
