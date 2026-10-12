import os
import sys
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import engine

def run_migration():
    print("=== Starting Database Migration: account_name on product_attributes ===")
    with engine.begin() as conn:
        # 1. Add account_name to product_attributes if not exists
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='product_attributes' AND column_name='account_name'
                ) THEN
                    ALTER TABLE product_attributes 
                    ADD COLUMN account_name accounttype NOT NULL DEFAULT 'JV';
                    RAISE NOTICE 'Added column account_name to product_attributes';
                END IF;
            END $$;
        """))

        # 2. Add index on account_name
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_product_attributes_account_name 
            ON product_attributes (account_name);
        """))

        # 3. Add unique constraint (product_ean, attribute_name, account_name)
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint 
                    WHERE conname = 'uq_product_attr_account'
                ) THEN
                    ALTER TABLE product_attributes 
                    ADD CONSTRAINT uq_product_attr_account 
                    UNIQUE (product_ean, attribute_name, account_name);
                    RAISE NOTICE 'Added unique constraint uq_product_attr_account';
                END IF;
            END $$;
        """))

        # 4. Add published_at to product_mappings if not exists
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='product_mappings' AND column_name='published_at'
                ) THEN
                    ALTER TABLE product_mappings 
                    ADD COLUMN published_at TIMESTAMP WITH TIME ZONE;
                    RAISE NOTICE 'Added column published_at to product_mappings';
                END IF;
            END $$;
        """))

    print("=== Migration completed successfully! ===")

if __name__ == "__main__":
    run_migration()
