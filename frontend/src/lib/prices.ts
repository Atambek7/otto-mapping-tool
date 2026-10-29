/** Same positive-price formats and Startpreis-only policy as the backend. */
export function parsePositivePrice(value: unknown): string {
  if (value == null || typeof value === "boolean") return "";
  let clean = String(value).replace(/EUR|€/gi, "").replace(/\s/g, "");
  if (clean.includes(".") && clean.includes(",")) {
    clean = clean.lastIndexOf(",") > clean.lastIndexOf(".")
      ? clean.replace(/\./g, "").replace(",", ".")
      : clean.replace(/,/g, "");
  } else {
    clean = clean.replace(",", ".");
  }
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(clean)) return "";
  const number = Number(clean);
  return Number.isFinite(number) && number > 0.0001
    ? (Number.isInteger(number) ? String(number) : number.toFixed(2))
    : "";
}

export function getAftercoolPrice(row: Record<string, unknown> = {}): string {
  return parsePositivePrice(row.Startpreis);
}
