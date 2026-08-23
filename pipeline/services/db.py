import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set. Add it to .env or environment.")
    return psycopg2.connect(dsn)
