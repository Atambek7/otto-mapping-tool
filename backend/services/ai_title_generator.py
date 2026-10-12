import os
import asyncio
import logging
from typing import Optional, Dict, List, Any
from sqlalchemy.orm import Session
import models
from services.automapper import get_openai_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du bist ein professioneller E-Commerce-Texter für Otto. "
    "Erstelle prägnante, natürliche Produkttitel auf Deutsch bis maximal 70 Zeichen.\n"
    "WICHTIGSTE REGEL: Behalte den genauen Möbeltyp des Originaltitels zwingend bei "
    "(z.B. Sessel bleibt Sessel, Sofa bleibt Sofa, Tisch bleibt Tisch, Bett bleibt Bett, Stuhl bleibt Stuhl). "
    "Wechsle niemals den Produkttyp und erfinde keine anderen Möbel."
)

FURNITURE_FAMILIES: Dict[str, set] = {
    "sessel": {"sessel", "relaxsessel", "fernsehsessel", "ohrensessel", "clubsessel", "kinosessel", "armlehnensessel", "liegesessel", "reklainer"},
    "sofa": {"sofa", "couch", "ecksofa", "wohnlandschaft", "polstergarnitur", "sofagarnitur", "kinosofa", "schlafsofa", "couchgarnitur", "2-sitzer", "3-sitzer", "4-sitzer"},
    "esstisch": {"esstisch", "essgruppe", "esszimmer-set", "esszimmertisch"},
    "couchtisch": {"couchtisch", "kaffeetisch", "beistelltisch", "wohnzimmertisch"},
    "stuhl": {"stuhl", "stühle", "esszimmerstuhl", "armlehnstuhl", "schwingstuhl", "barhocker", "polsterstuhl"},
    "bett": {"bett", "boxspringbett", "polsterbett", "doppelbett", "massivholzbett", "etagenbett", "schlafzimmer-set"},
    "schrank": {"schrank", "kleiderschrank", "drehtürenschrank", "schwebetürenschrank", "garderobenschrank", "sideboard", "highboard", "lowboard", "vitrine", "kommode"},
}

def detect_furniture_types(text: str) -> set:
    if not text:
        return set()
    lower = text.lower()
    found = set()
    for fam, kws in FURNITURE_FAMILIES.items():
        for kw in kws:
            if kw in lower:
                found.add(fam)
                break
    return found

def validate_title_type_consistency(original_title: str, generated_title: str) -> bool:
    """
    Guarantees that AI-generated title maintains the exact furniture type of the original title.
    For example: an armchair (Sessel) cannot become a dining table (Esstisch) or a bed (Bett).
    """
    if not original_title or not generated_title:
        return False
    orig_types = detect_furniture_types(original_title)
    gen_types = detect_furniture_types(generated_title)

    # If original title contains recognized furniture types:
    if orig_types:
        # If generated title contains recognized types, at least one must overlap
        if gen_types and not (orig_types & gen_types):
            logger.warning(
                f"[TITLE CONSISTENCY GUARD] Furniture type mismatch rejected: "
                f"original '{original_title}' (types: {orig_types}) vs generated '{generated_title}' (types: {gen_types})"
            )
            return False

    return True

def is_valid_source_title(title: Optional[str]) -> bool:
    """
    Validates that a source title is real, meaningful, and not empty/placeholder.
    """
    if not title:
        return False
    clean = title.strip()
    if len(clean) < 4:
        return False
    lower = clean.lower()
    if lower in ("без названия", "null", "none", "n/a", "undefined", "product", "titel", "title", "-"):
        return False
    if lower.startswith("product ") and clean[8:].strip().isdigit():
        return False
    return True

def _truncate_title_safely(title: str, max_len: int = 70) -> str:
    """
    Safely truncates title to max_len without cutting words in half.
    Strips trailing punctuation.
    """
    if not title:
        return ""
    title = title.strip()
    if len(title) <= max_len:
        return title
    truncated = title[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 3:
        truncated = truncated[:last_space]
    return truncated.rstrip(" ,;:-.")

def resolve_openai_key(db: Optional[Session] = None, user: Optional[models.User] = None) -> Optional[str]:
    """
    Resolves an active OpenAI API key from user, environment, or database.
    """
    if user and user.openai_api_key:
        return user.openai_api_key.strip()
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    if db:
        user_with_key = db.query(models.User).filter(
            models.User.openai_api_key.isnot(None),
            models.User.openai_api_key != ""
        ).first()
        if user_with_key and user_with_key.openai_api_key:
            return user_with_key.openai_api_key.strip()
        acc_s = db.query(models.AccountSettings).filter(
            models.AccountSettings.openai_api_key.isnot(None),
            models.AccountSettings.openai_api_key != ""
        ).first()
        if acc_s and acc_s.openai_api_key:
            return acc_s.openai_api_key.strip()
    return None

async def generate_single_ai_title(
    api_key: str,
    current_title: str,
    category_name: str = "Möbel",
    max_retries: int = 2
) -> Optional[str]:
    """
    Generates a natural German product title up to 70 characters using gpt-4o-mini.
    Returns the title string or None on failure.
    """
    if not api_key:
        return None
    
    clean_title = (current_title or "").strip()
    if not is_valid_source_title(clean_title):
        logger.warning(f"Aborting AI title generation for invalid/empty source title: '{clean_title}'")
        return None

    client = get_openai_client(api_key)

    prompt = (
        f"Titel: {clean_title}\n"
        f"Kategorie: {category_name or 'Möbel'}\n\n"
        "Regeln:\n"
        "1. Natürlicher Produkttitel auf Deutsch bis maximal 70 Zeichen.\n"
        "2. Behalte den exakten Möbeltyp aus dem Originaltitel bei (z.B. Sessel bleibt Sessel, Sofa bleibt Sofa). Niemals einen anderen Produkttyp erfinden.\n"
        "3. Beginne NICHT mit der Kategorie.\n"
        "4. Kein Keyword-Stuffing: keine Wortwiederholungen, keine sinnlose Aneinanderreihung von Suchbegriffen.\n"
        "5. Verwende nur wesentliche Produkteigenschaften.\n"
        "6. Keine erfundenen Daten, keine Werbefüllwörter (wie Premium, Top, Hochwertig).\n"
        "7. Antworte NUR mit dem fertigen Titel, ohne Anführungszeichen, ohne Erklärungen."
    )

    for attempt in range(max_retries):
        try:
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=40,
                temperature=0.3
            )
            content = (resp.choices[0].message.content or "").strip()
            cleaned = _truncate_title_safely(content, max_len=70)
            if cleaned and len(cleaned) >= 3 and not cleaned.lower().startswith("entschuldigung"):
                if validate_title_type_consistency(clean_title, cleaned):
                    return cleaned
                else:
                    logger.warning(f"Generated title failed type consistency check: '{cleaned}' vs original '{clean_title}'")
                    continue
            logger.warning(f"Model returned invalid or short title: '{content}'")
        except Exception as ex:
            logger.warning(f"Error calling OpenAI for title generation (attempt {attempt + 1}/{max_retries}): {ex}")
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)

    return None

async def generate_ai_titles_for_mappings(
    mappings: List[models.ProductMapping],
    db: Session,
    api_key: str,
    cat_names_map: Optional[Dict[str, str]] = None,
    concurrency: int = 15
) -> tuple[Dict[str, str], Dict[str, str]]:
    """
    Concurrently generates AI titles for a batch of ProductMapping models.
    Updates mappings in-place and commits changes to DB.
    Returns (updated_map, errors_map).
    """
    if not mappings or not api_key:
        return {}, {m.product_ean: "No API key" for m in mappings}

    cat_names_map = cat_names_map or {}
    sem = asyncio.Semaphore(concurrency)
    updated_map: Dict[str, str] = {}
    errors_map: Dict[str, str] = {}

    async def worker(pm: models.ProductMapping):
        ean = pm.product_ean
        title = pm.product_name or ""
        cat_name = cat_names_map.get(str(pm.category_id), "") or "Möbel"
        async with sem:
            res = await generate_single_ai_title(api_key, title, category_name=cat_name)
            if res:
                updated_map[ean] = res
            else:
                errors_map[ean] = "Failed to generate title"

    await asyncio.gather(*(worker(m) for m in mappings))

    if updated_map:
        import datetime
        now = datetime.datetime.utcnow()
        for pm in mappings:
            if pm.product_ean in updated_map:
                orig_cat = pm.category_id
                orig_st = pm.status
                pm.product_name = updated_map[pm.product_ean]
                pm.has_ai_title = True
                pm.last_synced_at = now
                # Safety guard: ensure category_id is never lost
                if orig_cat and not pm.category_id:
                    pm.category_id = orig_cat
                if orig_st and orig_st in ("Ready", "Need Attributes", "Error"):
                    pm.status = orig_st
        db.commit()

    return updated_map, errors_map
