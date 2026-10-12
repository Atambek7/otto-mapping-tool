"""Initialize an empty Postgres database for CI.

Models declare Postgres ENUM columns with create_type=False (the types are
normally created by Alembic migration 0001), so on a brand-new database the
enum types must exist before Base.metadata.create_all() runs.

Used ONLY by GitHub Actions against a throwaway Postgres service container.
"""
from sqlalchemy import text

from database import Base, engine
import models  # noqa: F401  (registers all tables on Base.metadata)

ENUMS = {
    "accounttype": ("JV", "XL"),
    "planstatus": ("draft", "active", "paused", "completed", "cancelled"),
    "queueitemstatus": ("pending", "published", "failed"),
    "executortype": ("otto_api", "batch_file"),
}


def main() -> None:
    with engine.begin() as conn:
        for name, values in ENUMS.items():
            exists = conn.execute(
                text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}
            ).scalar()
            if not exists:
                labels = ", ".join(f"'{v}'" for v in values)
                conn.execute(text(f"CREATE TYPE {name} AS ENUM ({labels})"))
    Base.metadata.create_all(bind=engine)
    print("CI database schema initialized")


if __name__ == "__main__":
    main()
