import { z } from "zod";

/**
 * 对应 Python 中的 ProjectItem Pydantic Schema
 * 代表从 zwdt 平台抓取到的政策项目数据结构
 */
export const crawlerProjectSchema = z.object({
  url: z.string(),
  project_id: z.string(),
  policy_id: z.string(),
  policy_basis: z.string(),
  policy_ids: z.array(z.string()).default([]),
  title: z.string(),
  region: z.string(),
  agency: z.string(),
  recipient: z.string(),
  start_date: z.string().nullable().optional(),
  end_date: z.string().nullable().optional(),
  content: z.string(),
  policy_object: z.string(),
  condition_text: z.string(),
  support_text: z.string(),
  material_text: z.string(),
  contact_information: z.string(),
  apply_method: z.string(),
  crawled_at: z.string(),
  raw_detail: z.record(z.any()),
});

export type CrawlerProject = z.infer<typeof crawlerProjectSchema>;

/**
 * 对应 Python 中的 PolicyItem Pydantic Schema
 * 代表从上海市政府官网抓取到的政策原文数据结构
 */
export const crawlerPolicySchema = z.object({
  url: z.string().nullable().optional(),
  policy_id: z.string(),
  project_ids: z.array(z.string()).default([]),
  title: z.string(),
  region: z.string(),
  document_number: z.string(),
  agency: z.string(),
  publish_date: z.string().nullable().optional(),
  expire_date: z.string().nullable().optional(),
  content: z.string(),
  pdf_url: z.string().nullable().optional(),
  raw_detail: z.record(z.any()),
});

export type CrawlerPolicy = z.infer<typeof crawlerPolicySchema>;
