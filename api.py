import sqlite3, os
from fastapi import FastAPI
from pydantic import BaseModel

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lojas.db")
app = FastAPI()

class Query(BaseModel):
    sql: str

@app.post("/query")
def query(q: Query):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(q.sql).fetchall()
    finally:
        conn.close()
