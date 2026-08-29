import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Draft, ProductMapping, ProductAttribute, CategoryCache, CategoryAttributeCache, AccountSettings

def migrate():
    print("Starting migration from SQLite to PostgreSQL...")
    
    sqlite_url = "sqlite:///./mapping_tool.db"
    pg_url = os.environ.get("DATABASE_URL")
    
    if not pg_url or not pg_url.startswith("postgresql"):
        print("Error: DATABASE_URL environment variable is not set to a postgresql URL.")
        print("DATABASE_URL =", pg_url)
        return

    # Create engines
    sqlite_engine = create_engine(sqlite_url)
    pg_engine = create_engine(pg_url)

    # Create all tables in Postgres
    print("Creating tables in PostgreSQL...")
    Base.metadata.create_all(pg_engine)

    SqliteSession = sessionmaker(bind=sqlite_engine)
    PgSession = sessionmaker(bind=pg_engine)

    sqlite_db = SqliteSession()
    pg_db = PgSession()

    try:
        tables = [
            (User, "users"),
            (AccountSettings, "account_settings"),
            (CategoryCache, "categories"),
            (CategoryAttributeCache, "category_attributes"),
            (Draft, "drafts"),
            (ProductMapping, "product_mappings"),
            (ProductAttribute, "product_attributes"),
        ]

        for Model, name in tables:
            print(f"Migrating {name}...")
            # Delete existing in PG (if we are re-running)
            pg_db.query(Model).delete()
            
            # Read from SQLite
            records = sqlite_db.query(Model).all()
            
            # Create detached copies
            for r in records:
                pg_db.merge(r)
                
            pg_db.commit()
            print(f"Migrated {len(records)} records for {name}.")

        print("Migration completed successfully!")

    except Exception as e:
        print("Error during migration:", e)
        pg_db.rollback()
    finally:
        sqlite_db.close()
        pg_db.close()

if __name__ == "__main__":
    migrate()
