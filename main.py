from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import List
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="UV Monitoring API")

# ==========================
# CORS (permite conexiones externas)
# ==========================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================
# Modelo de datos
# ==========================

class UVData(BaseModel):
    device_id: str
    uv_index: float
    alarm_triggered: bool
    timestamp: datetime

# ==========================
# Base temporal en memoria (luego la cambiaremos por base real)
# ==========================

uv_storage: List[UVData] = []

# ==========================
# Endpoint raíz
# ==========================

@app.get("/")
def read_root():
    return {"status": "UV API funcionando correctamente 🚀"}

# ==========================
# Recibir datos del ESP32
# ==========================

@app.post("/uv-data")
def receive_uv_data(data: UVData):
    uv_storage.append(data)

    return {
        "message": "Datos recibidos correctamente",
        "device_id": data.device_id,
        "uv_index": data.uv_index,
        "alarm_triggered": data.alarm_triggered,
        "timestamp": data.timestamp
    }

# ==========================
# Obtener datos por dispositivo
# ==========================

@app.get("/uv-data/{device_id}")
def get_device_data(device_id: str):
    device_records = [d for d in uv_storage if d.device_id == device_id]

    return {
        "device_id": device_id,
        "total_records": len(device_records),
        "data": device_records
    }

# ==========================
# Reporte semanal básico
# ==========================

@app.get("/weekly-report/{device_id}")
def weekly_report(device_id: str):
    one_week_ago = datetime.utcnow() - timedelta(days=7)

    records = [
        d for d in uv_storage
        if d.device_id == device_id and d.timestamp >= one_week_ago
    ]

    if not records:
        return {
            "device_id": device_id,
            "message": "No hay datos esta semana"
        }

    total_exposure = sum(d.uv_index for d in records)
    alarm_count = sum(1 for d in records if d.alarm_triggered)
    max_uv = max(d.uv_index for d in records)

    return {
        "device_id": device_id,
        "records_last_7_days": len(records),
        "total_uv_exposure": round(total_exposure, 2),
        "max_uv_index": max_uv,
        "alarm_trigger_count": alarm_count
    }
