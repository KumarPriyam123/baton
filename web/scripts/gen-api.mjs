// Regenerates src/api/generated.ts from the running API's OpenAPI document.
// The stack must be up. OPENAPI_URL overrides the default (e.g. when WEB_PORT is not 8080).
import { spawnSync } from "node:child_process";

const url = process.env.OPENAPI_URL ?? "http://localhost:8080/api/openapi.json";
const result = spawnSync(
  "npx",
  ["--no-install", "openapi-typescript", url, "-o", "src/api/generated.ts"],
  {
    stdio: "inherit",
    shell: true,
  },
);
process.exit(result.status ?? 1);
