import { jsonSchemaToZod } from "json-schema-to-zod";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");

const projectPath = path.join(rootDir, "schemas", "project.json");
const policyPath = path.join(rootDir, "schemas", "policy.json");
const outputPath = path.join(rootDir, "crawler.ts");

try {
  const projectSchemaRaw = JSON.parse(fs.readFileSync(projectPath, "utf-8"));
  const policySchemaRaw = JSON.parse(fs.readFileSync(policyPath, "utf-8"));

  // json-schema-to-zod generates a string representing the zod code
  const projectZodCode = jsonSchemaToZod(projectSchemaRaw, {
    name: "crawlerProjectSchema",
    module: "none",
  });
  const policyZodCode = jsonSchemaToZod(policySchemaRaw, {
    name: "crawlerPolicySchema",
    module: "none",
  });

  const fileContent = `import { z } from "zod";

/**
 * Automatically generated from Pydantic models (models.py) via export_schema.py.
 * DO NOT EDIT MANUALLY.
 */

export ${projectZodCode};
export type CrawlerProject = z.infer<typeof crawlerProjectSchema>;

export ${policyZodCode};
export type CrawlerPolicy = z.infer<typeof crawlerPolicySchema>;
`;

  fs.writeFileSync(outputPath, fileContent, "utf-8");
  console.log("✓ Successfully generated crawler.ts from JSON Schema!");
} catch (err) {
  console.error("Failed to generate schemas:", err);
  process.exit(1);
}
