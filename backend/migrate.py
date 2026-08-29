import sqlite3

def migrate():
    conn = sqlite3.connect('mapping_tool.db')
    cursor = conn.cursor()
    
    # 1. Add account_name to product_mappings if not exists
    try:
        cursor.execute("ALTER TABLE product_mappings ADD COLUMN account_name VARCHAR DEFAULT 'JV'")
        print("Added account_name column to product_mappings")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column account_name already exists.")
        else:
            print("Error altering product_mappings:", e)

    # 1.5 Add read-model columns to product_mappings
    columns_to_add = [
        ("product_name", "VARCHAR"),
        ("price", "VARCHAR"),
        ("image_url", "VARCHAR"),
        ("last_synced_at", "DATETIME")
    ]
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE product_mappings ADD COLUMN {col_name} {col_type}")
            print(f"Added {col_name} column to product_mappings")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Column {col_name} already exists.")
            else:
                print(f"Error adding {col_name}:", e)

    # 2. Create account_settings table
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name VARCHAR UNIQUE,
                otto_client_id VARCHAR,
                otto_client_secret VARCHAR,
                aftercool_token VARCHAR
            )
        """)
        
        # Insert default rows
        cursor.execute("INSERT OR IGNORE INTO account_settings (account_name) VALUES ('JV')")
        cursor.execute("INSERT OR IGNORE INTO account_settings (account_name) VALUES ('XL')")
        
        print("Created account_settings table")
    except sqlite3.OperationalError as e:
        print("Error creating account_settings:", e)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()
