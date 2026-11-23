export const MAX_PREPARED_TITLE_CODEPOINTS = 70;

/** Matches Python len() and PostgreSQL char_length() for Unicode code points. */
export function titleCodePointLength(value: string): number {
  return Array.from(value).length;
}

const LOW_SIGNAL_TERMS = new Set(["design", "luxus", "luxury", "neu", "new", "exklusiv", "hochwertig"]);
const PRODUCT_SHAPE_TERMS = new Set([
  "sofa", "couch", "sofas", "couches", "sofagarnitur", "sofaset", "ohrensessel", "sessel", "garnitur", "polsterstuhl", "stuhl",
]);

/** Conservative demo-only compression matching the server policy; never truncates identifiers or words. */
export function safelyCompactSourceTitle(value: string, limit = MAX_PREPARED_TITLE_CODEPOINTS): string | null {
  const source = value.replace(/\s+/gu, " ").trim();
  if (!source) return null;
  if (titleCodePointLength(source) <= limit) return source;
  const tokens = source.split(" ");
  const keyOf = (token: string) => token.replace(/^\W+|\W+$/gu, "").toLocaleLowerCase("de");
  const keys = tokens.map(keyOf);
  const hasSofaCompound = keys.some((key) => key === "sofagarnitur" || key === "sofaset");
  const hasProductShape = keys.some((key) => PRODUCT_SHAPE_TERMS.has(key));
  const kept: string[] = [];
  const seen = new Set<string>();
  let seenSofaSynonym = false;

  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    const key = keys[i];
    if (!key || LOW_SIGNAL_TERMS.has(key)) continue;
    if (key === "polster" && hasProductShape) continue;
    if (hasSofaCompound && (key === "sofa" || key === "garnitur")) continue;
    if (["sofa", "couch", "sofas", "couches"].includes(key)) {
      if (seenSofaSynonym) continue;
      seenSofaSynonym = true;
    }
    const isIdentity = /\d/u.test(token) || token.startsWith("#") || token.startsWith("@") || (token.length > 1 && token === token.toLocaleUpperCase("de"));
    if (seen.has(key) && !isIdentity) continue;
    kept.push(token);
    seen.add(key);
  }
  const candidate = kept.join(" ").replace(/\s+/gu, " ").trim();
  return candidate && titleCodePointLength(candidate) <= limit ? candidate : null;
}
