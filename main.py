from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import os
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime, func
from sqlalchemy.orm import sessionmaker, declarative_base

# ==========================
# Configuración base
# ==========================

app = FastAPI(title="UV Monitoring API - Professional Version")

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

Base.metadata.create_all(bind=engine)

# ==========================
# Modelo de entrada
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
    return {"status": "UV API funcionando correctamente 🚀"}

# ==========================
# Guardar datos
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

    return {"message": "Datos guardados en PostgreSQL ✅"}

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
            "uv_index": r.uv_index,
            "alarm_triggered": r.alarm_triggered,
            "timestamp": r.timestamp
        }
        for r in records
    ]

# ==========================
# Resumen estadístico
# ==========================

@app.get("/summary")
def get_summary():
    db = SessionLocal()

    total_records = db.query(UVData).count()
    avg_uv = db.query(func.avg(UVData.uv_index)).scalar()
    max_uv = db.query(func.max(UVData.uv_index)).scalar()
    alarms = db.query(UVData)\
               .filter(UVData.alarm_triggered == True)\
               .count()

    db.close()

    return {
        "total_records": total_records,
        "avg_uv": avg_uv,
        "max_uv": max_uv,
        "alarms_triggered": alarms
    }

# ==========================
# Cálculo de dosis acumulada
# ==========================

@app.get("/dose")
def calculate_dose():
    db = SessionLocal()

    records = db.query(UVData)\
                .order_by(UVData.timestamp.asc())\
                .all()

    if len(records) < 2:
        db.close()
        return {"message": "No hay suficientes datos"}

    total_dose = 0

    for i in range(len(records) - 1):
        uv = records[i].uv_index
        t1 = records[i].timestamp
        t2 = records[i + 1].timestamp

        delta_seconds = (t2 - t1).total_seconds()
        total_dose += uv * delta_seconds

    db.close()

    # Clasificación de riesgo
    if total_dose < 5000:
        risk = "BAJO"
        recommendation = "Exposición segura"
    elif total_dose < 15000:
        risk = "MODERADO"
        recommendation = "Considerar protección solar"
    elif total_dose < 30000:
        risk = "ALTO"
        recommendation = "Usar protección y limitar exposición"
    else:
        risk = "CRÍTICO"
        recommendation = "Evitar exposición inmediata"

    return {
        "total_dose": round(total_dose, 2),
        "risk_level": risk,
        "recommendation": recommendation
    }

# ==========================
# Horas más peligrosas
# ==========================

@app.get("/danger-hours")
def danger_hours():
    db = SessionLocal()

    results = db.query(
        func.extract("hour", UVData.timestamp).label("hour"),
        func.avg(UVData.uv_index).label("avg_uv")
    ).group_by("hour").order_by(func.avg(UVData.uv_index).desc()).all()

    db.close()

    if not results:
        return {"message": "No hay datos"}

    most_dangerous_hour = results[0]

    return {
        "most_dangerous_hour": int(most_dangerous_hour.hour),
        "average_uv_at_that_hour": round(most_dangerous_hour.avg_uv, 2),
        "ranking": [
            {
                "hour": int(r.hour),
                "avg_uv": round(r.avg_uv, 2)
            }
            for r in results
        ]
    }

