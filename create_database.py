import sqlite3

conn = sqlite3.connect("sales.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY,
    customer_name TEXT NOT NULL,
    country TEXT NOT NULL,
    segment TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS sales (
    sale_id INTEGER PRIMARY KEY,
    sale_date TEXT NOT NULL,
    customer_id INTEGER NOT NULL,
    product TEXT NOT NULL,
    revenue REAL NOT NULL,
    cost REAL NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
)
""")

customers = [
    (1, "Alpine Retail", "Switzerland", "Enterprise"),
    (2, "Maison Nova", "France", "SMB"),
    (3, "Benelux Direct", "Belgium", "Mid-Market"),
    (4, "Lux Partners", "Luxembourg", "Enterprise"),
    (5, "Helvetia Digital", "Switzerland", "SMB"),
]

sales = [
    (1, "2026-01-15", 1, "Analytics Pro", 12000, 5000),
    (2, "2026-02-10", 2, "Data Studio", 8500, 4000),
    (3, "2026-03-08", 3, "Analytics Pro", 10400, 4300),
    (4, "2026-04-11", 4, "Advisory Pack", 15000, 7500),
    (5, "2026-05-06", 5, "Data Studio", 9200, 4100),
    (6, "2026-06-20", 1, "Advisory Pack", 17500, 8000),
    (7, "2026-07-17", 2, "Analytics Pro", 11200, 4700),
    (8, "2026-08-04", 3, "Data Studio", 9800, 4200),
    (9, "2026-09-12", 5, "Analytics Pro", 13800, 5600),
    (10, "2026-09-28", 4, "Advisory Pack", 16200, 7600),
]

cursor.executemany(
    "INSERT OR REPLACE INTO customers VALUES (?, ?, ?, ?)",
    customers
)

cursor.executemany(
    "INSERT OR REPLACE INTO sales VALUES (?, ?, ?, ?, ?, ?)",
    sales
)

conn.commit()
conn.close()

print("Database created successfully.")
