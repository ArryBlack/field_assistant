import psycopg2.extras
from db import get_conn

def check_trips():
    conn = get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM trips ORDER BY trip_id DESC;")
        rows = cur.fetchall()
        
        if not rows:
            print("The trips table is currently empty.")
            return

        print(f"Found {len(rows)} trip(s):\n")
        for row in rows:
            print(f"Trip ID: {row['trip_id']}")
            print(f"User ID: {row['telegram_user_id']}")
            print(f"Destination: {row['destination']}")
            print(f"Guide: {row['guide']}")
            print(f"Target Species: {row['target_species']}")
            print(f"Context: {row['context']}")
            print(f"Start: {row['start_date']}")
            print(f"End: {row['end_date']}")
            print("-" * 40)

if __name__ == "__main__":
    check_trips()