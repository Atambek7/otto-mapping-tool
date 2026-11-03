// Web Crypto HMAC-SHA256 session management for OttoRestart
// Compatible with both Edge and Node.js runtimes

export const SESSION_COOKIE = "ottorestart_session";
export const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60; // 7 days

export interface AuthConfig {
  user: string;
  password: string;
  secret: string;
}

export function getAuthConfig(): AuthConfig {
  return {
    user: process.env.OTTORESTART_AUTH_USER || "admin",
    password: process.env.OTTORESTART_AUTH_PASSWORD || "admin",
    secret:
      process.env.OTTORESTART_AUTH_SECRET ||
      "ottorestart-dev-secret-key-32-chars-long-min!!",
  };
}

async function getHmacKey(secret: string): Promise<CryptoKey> {
  const enc = new TextEncoder();
  return crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );
}

function bufferToHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return hex;
}

function hexToBuffer(hex: string): Uint8Array | null {
  if (hex.length % 2 !== 0) return null;
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    const val = parseInt(hex.substring(i, i + 2), 16);
    if (isNaN(val)) return null;
    bytes[i / 2] = val;
  }
  return bytes;
}

/**
 * Constant-time string comparison for passwords and tokens
 */
export function timingSafeEqualString(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

/**
 * Generates signed session token: `username.expiryTimestamp.signatureHex`
 */
export async function createSessionToken(
  user: string,
  ttlSeconds: number = SESSION_TTL_SECONDS
): Promise<string> {
  const config = getAuthConfig();
  const exp = Math.floor(Date.now() / 1000) + ttlSeconds;
  const payload = `${user}.${exp}`;
  const key = await getHmacKey(config.secret);
  const enc = new TextEncoder();
  const sigBuffer = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  const sigHex = bufferToHex(sigBuffer);
  return `${payload}.${sigHex}`;
}

/**
 * Verifies signed session token. Returns username if valid and not expired, null otherwise.
 */
export async function verifySessionToken(
  token: string | undefined | null
): Promise<{ user: string } | null> {
  if (!token || typeof token !== "string") return null;

  const parts = token.split(".");
  if (parts.length !== 3) return null;

  const [user, expStr, sigHex] = parts;
  const exp = parseInt(expStr, 10);
  if (isNaN(exp)) return null;

  // Check expiration
  const now = Math.floor(Date.now() / 1000);
  if (now > exp) return null;

  const config = getAuthConfig();
  const payload = `${user}.${expStr}`;
  const key = await getHmacKey(config.secret);
  const enc = new TextEncoder();
  const sigBytes = hexToBuffer(sigHex);
  if (!sigBytes) return null;

  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    sigBytes as unknown as BufferSource,
    enc.encode(payload)
  );

  if (!valid) return null;

  return { user };
}
