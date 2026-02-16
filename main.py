from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime

app = FastAPI()

class UVData(BaseModel):
    user_id: str
    uv_index: float
    alarm_triggered: bool
    timestamp: datetime

@app.get("/")
def root():
    return {"status": "UV API funcionando"}

@app.post("/uv-data")
def receive_data(data: UVData):
    return {"message": "Datos recibidos"}
