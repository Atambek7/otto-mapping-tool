// Web Crypto HMAC-SHA256 session management for OttoRestart
// Compatible with both Edge and Node.js runtimes

export const SESSION_COOKIE = "ottorestart_session";
export const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60; // 7 days

export interface PlainPasswordCredential {
  scheme: "plain";
  password: string;
}

export interface Pbkdf2PasswordCredential {
  scheme: "pbkdf2-sha256";
  iterations: number;
  salt: string;
  digest: string;
}

export type PasswordCredential = PlainPasswordCredential | Pbkdf2PasswordCredential;

export interface AuthConfig {
  users: Readonly<Record<string, PasswordCredential>>;
  secret: string;
}

const USERNAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/;
const MIN_PBKDF2_ITERATIONS = 100_000;
const MAX_PBKDF2_ITERATIONS = 2_000_000;

function decodeBase64Url(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error("Invalid OttoRestart credential encoding.");
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function parseHashedUsers(encoded: string): Record<string, PasswordCredential> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(decodeBase64Url(encoded)));
  } catch {
    throw new Error("OttoRestart user credential map is invalid.");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("OttoRestart user credential map is invalid.");
  }

  const users: Record<string, PasswordCredential> = Object.create(null);
  const lowerCaseNames = new Set<string>();
  for (const [username, rawCredential] of Object.entries(parsed)) {
    if (!USERNAME_PATTERN.test(username) || lowerCaseNames.has(username.toLowerCase())) {
      throw new Error("OttoRestart user credential map contains an invalid or duplicate login.");
    }
    lowerCaseNames.add(username.toLowerCase());
    if (!rawCredential || typeof rawCredential !== "object" || Array.isArray(rawCredential)) {
      throw new Error("OttoRestart user credential map contains an invalid credential.");
    }
    const credential = rawCredential as Record<string, unknown>;
    if (credential.scheme !== "pbkdf2-sha256" ||
        !Number.isInteger(credential.iterations) ||
        Number(credential.iterations) < MIN_PBKDF2_ITERATIONS ||
        Number(credential.iterations) > MAX_PBKDF2_ITERATIONS ||
        typeof credential.salt !== "string" || typeof credential.digest !== "string") {
      throw new Error("OttoRestart user credential map contains an invalid password hash.");
    }
    const salt = decodeBase64Url(credential.salt);
    const digest = decodeBase64Url(credential.digest);
    if (salt.length < 16 || salt.length > 64 || digest.length !== 32) {
      throw new Error("OttoRestart user credential map contains an invalid password hash.");
    }
    users[username] = {
      scheme: "pbkdf2-sha256",
      iterations: Number(credential.iterations),
      salt: credential.salt,
      digest: credential.digest,
    };
  }
  if (Object.keys(users).length === 0 || Object.keys(users).length > 100) {
    throw new Error("OttoRestart user credential map must contain between 1 and 100 logins.");
  }
  return users;
}

export function getAuthConfig(): AuthConfig {
  const encodedUsers = process.env.OTTORESTART_AUTH_USERS_B64?.trim();
  const legacyUser = process.env.OTTORESTART_AUTH_USER || "admin";
  const legacyPassword = process.env.OTTORESTART_AUTH_PASSWORD || "admin";
  const users = encodedUsers
    ? parseHashedUsers(encodedUsers)
    : { [legacyUser]: { scheme: "plain", password: legacyPassword } as const };
  const config = {
    users,
    secret:
      process.env.OTTORESTART_AUTH_SECRET ||
      "ottorestart-dev-secret-key-32-chars-long-min!!",
  };

  if (process.env.NODE_ENV === "production") {
    const weakSecret = config.secret.length < 32 || /change-this|replace-with|example|default-secret|dev-secret/i.test(config.secret);
    const hasHashedUsers = Boolean(encodedUsers);
    const hasLegacyCredentials = Boolean(process.env.OTTORESTART_AUTH_USER?.trim() && process.env.OTTORESTART_AUTH_PASSWORD?.trim());
    const hasDefaultLegacyLogin = hasHashedUsers
      ? Object.keys(users).some((username) => username.toLowerCase() === "admin")
      : legacyUser.toLowerCase() === "admin" || legacyPassword.toLowerCase() === "admin";
    if ((!hasHashedUsers && !hasLegacyCredentials) || hasDefaultLegacyLogin || weakSecret) {
      throw new Error("OttoRestart production authentication is not configured safely.");
    }
  }
  return config;
}

const DUMMY_PASSWORD_CREDENTIAL: Pbkdf2PasswordCredential = {
  scheme: "pbkdf2-sha256",
  iterations: 600_000,
  salt: "AAAAAAAAAAAAAAAAAAAAAA",
  digest: "A".repeat(43),
};

function timingSafeEqualBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let index = 0; index < a.length; index += 1) result |= a[index] ^ b[index];
  return result === 0;
}

async function verifyPassword(password: string, credential: PasswordCredential): Promise<boolean> {
  if (credential.scheme === "plain") return timingSafeEqualString(password, credential.password);
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const derived = new Uint8Array(await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: decodeBase64Url(credential.salt) as unknown as BufferSource,
      iterations: credential.iterations,
    },
    key,
    256,
  ));
  return timingSafeEqualBytes(derived, decodeBase64Url(credential.digest));
}

/** Authenticate a submitted username/password pair using the configured map. */
export async function verifyLoginCredentials(username: string, password: string, config: AuthConfig): Promise<boolean> {
  const credential = Object.hasOwn(config.users, username) ? config.users[username] : undefined;
  const passwordMatches = await verifyPassword(password, credential ?? DUMMY_PASSWORD_CREDENTIAL);
  return Boolean(credential) && passwordMatches;
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
  if (now >= exp) return null;

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
