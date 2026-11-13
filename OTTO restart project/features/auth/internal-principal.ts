import { createHmac, randomUUID } from "node:crypto";
import { getAuthConfig } from "./session";

const INTERNAL_ISSUER = "ottorestart-bff";
const INTERNAL_AUDIENCE = "otto-restart-api";
const ALLOWED_ACCOUNTS = new Set(["JV", "XL"]);

type SessionPrincipal = { user: string };

function configuredAccounts(): string[] {
  const raw = process.env.OTTORESTART_OPERATOR_ACCOUNTS ?? "JV,XL";
  const accounts = [...new Set(raw.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean))];
  if (accounts.length === 0 || accounts.some((account) => !ALLOWED_ACCOUNTS.has(account))) {
    throw new Error("OttoRestart operator account configuration is invalid.");
  }
  if (process.env.NODE_ENV === "production" && !process.env.OTTORESTART_OPERATOR_ACCOUNTS?.trim()) {
    throw new Error("OttoRestart operator accounts must be configured in production.");
  }
  return accounts;
}

function configuredOperatorIds(logins: string[]): { ids: Set<string>; legacyId: string | null } {
  const legacyId = process.env.OTTORESTART_OPERATOR_ID?.trim() || null;
  const raw = process.env.OTTORESTART_OPERATOR_IDS?.trim();
  if (!raw) {
    if (process.env.NODE_ENV === "production" && !legacyId) {
      throw new Error("OttoRestart operator identity is not configured in production.");
    }
    return { ids: new Set(), legacyId };
  }

  const parts = raw.split(",").map((item) => item.trim());
  const ids = new Set(parts);
  if (parts.some((id) => !id || id.length > 128) || ids.size !== parts.length || logins.some((login) => !ids.has(login))) {
    throw new Error("OttoRestart operator login allow-list is invalid.");
  }
  return { ids, legacyId };
}

export function createInternalPrincipal(
  session: SessionPrincipal,
  csrfVerified: boolean,
  requestId = randomUUID(),
): string {
  const secret = process.env.OTTORESTART_INTERNAL_AUTH_SECRET?.trim();
  if (!secret || secret.length < 32 || /change-this|replace-with|example|default-secret|dev-secret/i.test(secret)) {
    throw new Error("OttoRestart internal authentication is not configured.");
  }
  const now = Math.floor(Date.now() / 1000);
  const logins = Object.keys(getAuthConfig().users);
  const { ids: operatorIds, legacyId: operatorId } = configuredOperatorIds(logins);
  if (!logins.includes(session.user) || (operatorIds.size > 0 && !operatorIds.has(session.user))) {
    throw new Error("OttoRestart session login is not configured as an operator.");
  }
  const subject = operatorIds.size > 0 ? session.user : operatorId || session.user;
  const claims = {
    iss: INTERNAL_ISSUER,
    aud: INTERNAL_AUDIENCE,
    kind: "operator",
    sub: subject,
    accounts: configuredAccounts(),
    permissions: ["restart:read", "restart:write"],
    rid: requestId,
    iat: now,
    exp: now + 60,
    csrf: csrfVerified,
  };
  const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const signature = createHmac("sha256", secret).update(payload).digest("base64url");
  return `${payload}.${signature}`;
}
