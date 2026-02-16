from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import os
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

# Crear app
app = FastAPI()

# Obtener DATABASE_URL desde Railway
DATABASE_URL = os.getenv("DATABASE_URL")

# Crear conexión
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# Modelo de tabla
class UVData(Base):
    __tablename__ = "uv_data"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String)
    uv_index = Column(Float)
    alarm_triggered = Column(Boolean)
    timestamp = Column(DateTime)

# Crear tabla automáticamente
Base.metadata.create_all(bind=engine)

# Modelo para recibir datos
class UVRequest(BaseModel):
    device_id: str
    uv_index: float
    alarm_triggered: bool
    timestamp: datetime

@app.get("/")
def root():
    return {"status": "UV API funcionando correctamente 🚀"}

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

