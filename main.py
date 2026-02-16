from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import os
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime, func
from sqlalchemy.orm import sessionmaker, declarative_base

# ==========================
# Crear app
# ==========================

app = FastAPI(title="UV Monitoring API")

# ==========================
# Conexión a PostgreSQL
# ==========================

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# ==========================
# Modelo de tabla
# ==========================

class UVData(Base):
    __tablename__ = "uv_data"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String)
    uv_index = Column(Float)
    alarm_triggered = Column(Boolean)
    timestamp = Column(DateTime)

# Crear tabla si no existe
Base.metadata.create_all(bind=engine)

# ==========================
# Modelo para recibir datos
# ==========================

class UVRequest(BaseModel):
    device_id: str
    uv_index: float
    alarm_triggered: bool
    timestamp: datetime

# ==========================
# Endpoint raíz
# ==========================

@app.get("/")
def root():
    return {"status": "UV API funcionando"}

# ==========================
# Guardar datos (ESP32)
# ==========================

@app.post("/data")
def receive_data(data: UVRequest):
    db = SessionLocal()

    new_record = UVData(
        device_id=data.device_id,
        uv_index=data.uv_index,
        alarm_triggered=data.alarm_triggered,
        timestamp=data.timestamp
    )

    db.add(new_record)
    db.commit()
    db.close()

    return {"message": "Datos guardados en PostgreSQL "}

# ==========================
# Ver todos los datos
# ==========================

@app.get("/all-data")
def get_all_data():
    db = SessionLocal()
    records = db.query(UVData).order_by(UVData.timestamp.desc()).all()
    db.close()

    return [
        {
            "id": r.id,
            "device_id": r.device_id,
            "uv_index": r.uv_index,
            "alarm_triggered": r.alarm_triggered,
            "timestamp": r.timestamp
        }
        for r in records
    ]

# ==========================
# Ver datos por dispositivo
# ==========================

@app.get("/data/{device_id}")
def get_data_by_device(device_id: str):
    db = SessionLocal()
    records = db.query(UVData)\
                .filter(UVData.device_id == device_id)\
                .order_by(UVData.timestamp.desc())\
                .all()
    db.close()

    return [
        {
            "id": r.id,
            "device_id": r.device_id,
            "uv_index": r.uv_index,
            "alarm_triggered": r.alarm_triggered,
            "timestamp": r.timestamp
        }
        for r in records
    ]

# ==========================
# Resumen estadístico básico
# ==========================

@app.get("/summary/{device_id}")
def get_summary(device_id: str):
    db = SessionLocal()

    total_records = db.query(UVData)\
                      .filter(UVData.device_id == device_id)\
                      .count()

    avg_uv = db.query(func.avg(UVData.uv_index))\
               .filter(UVData.device_id == device_id)\
               .scalar()

    max_uv = db.query(func.max(UVData.uv_index))\
               .filter(UVData.device_id == device_id)\
               .scalar()

    alarms = db.query(UVData)\
               .filter(UVData.device_id == device_id,
                       UVData.alarm_triggered == True)\
               .count()

    db.close()

    return {
        "device_id": device_id,
        "total_records": total_records,
        "avg_uv": avg_uv,
        "max_uv": max_uv,
        "alarms_triggered": alarms
    }

