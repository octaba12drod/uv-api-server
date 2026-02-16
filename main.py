from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import os
import psycopg2

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(DATABASE_URL)

# Crear tabla si no existe
def create_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS uv_data (
            id SERIAL PRIMARY KEY,
            device_id TEXT,
            uv_index FLOAT,
            alarm_triggered BOOLEAN,
            timestamp TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

create_table()

class UVData(BaseModel):
    device_id: str
    uv_index: float
    alarm_triggered: bool
    timestamp: datetime

@app.get("/")
def root():
    return {"status": "UV API funcionando correctamente 🚀"}

@app.post("/data")
def receive_data(data: UVData):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO uv_data (device_id, uv_index, alarm_triggered, timestamp)
        VALUES (%s, %s, %s, %s)
    """, (data.device_id, data.uv_index, data.alarm_triggered, data.timestamp))
    conn.commit()
    cur.close()
    conn.close()

    return {"message": "Datos guardados correctamente"}

@app.get("/data")
def get_all_data():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM uv_data ORDER BY timestamp DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {"data": rows}

