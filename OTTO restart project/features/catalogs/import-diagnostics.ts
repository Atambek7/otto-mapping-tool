const IMPORT_REASONS: Record<string, string> = {
  PROTECTED_FACTORY_MATCH: "Товар найден в OTTO, но совпадения Aftercool относятся к защищённым фабрикам из списка «не трогать». Автоматическое распределение остановлено.",
  FACTORY_AMBIGUOUS: "Aftercool вернул несколько фабрик; требуется уточнить источник товара.",
  SKU_EAN_MISMATCH: "EAN в файле не совпадает с EAN, подтверждённым OTTO для этого SKU.",
  OTTO_EAN_UNCONFIRMED: "OTTO не подтвердил EAN товара.",
  OTTO_PRICE_IDENTITY_UNRESOLVED: "Ответ цены OTTO не подтвердил выбранный SKU.",
  DUPLICATE_IN_FILE: "Повторная строка в этом файле.",
};

export function describeImportError(code: string): string {
  return IMPORT_REASONS[code] ? `${code}: ${IMPORT_REASONS[code]}` : code;
}

export function describeImportRunError(error: Record<string, unknown>): string {
  if (typeof error.code === "string") return describeImportError(error.code);
  if (error.rowStates && typeof error.rowStates === "object") {
    const states = error.rowStates as Record<string, unknown>;
    const count = (name: string) => typeof states[name] === "number" ? states[name] as number : 0;
    return `Результат строк: принято ${count("VALID")}, требуют разбора ${count("IDENTITY_UNRESOLVED")}, ошибок ${count("ERROR") + count("INVALID")}, дублей ${count("DUPLICATE")}. Причины указаны в таблице ниже.`;
  }
  return "Импорт завершён с замечаниями. Подробности указаны в строках результата.";
}
