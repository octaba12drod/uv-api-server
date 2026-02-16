from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
import os
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from fastapi.responses import FileResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import Image
import matplotlib.pyplot as plt
import smtplib
from email.message import EmailMessage

app = FastAPI(title="UV Monitoring API - Advanced Version")

DATABASE_URL = os.getenv("DATABASE_URL")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# ==========================
# Modelo DB
# ==========================

class UVData(Base):
    __tablename__ = "uv_data"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String)
    uv_index = Column(Float)
    alarm_triggered = Column(Boolean)
    timestamp = Column(DateTime)

Base.metadata.create_all(bind=engine)

class UVRequest(BaseModel):
    device_id: str
    uv_index: float
    alarm_triggered: bool
    timestamp: datetime

# ==========================
# Guardar datos
# ==========================

@app.post("/data")
def receive_data(data: UVRequest):
    db = SessionLocal()
    new_record = UVData(**data.dict())
    db.add(new_record)
    db.commit()
    db.close()
    return {"message": "Datos guardados correctamente"}

# ==========================
# Generar gráfico
# ==========================

def generate_graph(records):
    timestamps = [r.timestamp for r in records]
    uv_values = [r.uv_index for r in records]

    plt.figure()
    plt.plot(timestamps, uv_values)
    plt.xlabel("Time")
    plt.ylabel("UV Index")
    plt.title("Weekly UV Exposure")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig("uv_graph.png")
    plt.close()

# ==========================
# Enviar email
# ==========================

def send_email_with_attachment(pdf_path, recipient):
    msg = EmailMessage()
    msg["Subject"] = "Weekly UV Exposure Report"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = recipient
    msg.set_content("Attached is your weekly UV exposure report.")

    with open(pdf_path, "rb") as f:
        file_data = f.read()
        msg.add_attachment(file_data, maintype="application",
                           subtype="pdf", filename="weekly_report.pdf")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)

# ==========================
# Generar PDF + Enviar email
# ==========================

@app.get("/weekly-report")
def weekly_report(recipient_email: str):
    db = SessionLocal()
    one_week_ago = datetime.utcnow() - timedelta(days=7)

    records = db.query(UVData)\
                .filter(UVData.timestamp >= one_week_ago)\
                .order_by(UVData.timestamp.asc())\
                .all()

    if len(records) < 2:
        db.close()
        return {"message": "No hay suficientes datos"}

    total_dose = 0
    alarms = 0
    max_uv = 0

    for i in range(len(records) - 1):
        uv = records[i].uv_index
        delta_seconds = (records[i+1].timestamp - records[i].timestamp).total_seconds()
        total_dose += uv * delta_seconds

        if uv > max_uv:
            max_uv = uv

        if records[i].alarm_triggered:
            alarms += 1

    avg_uv = sum(r.uv_index for r in records) / len(records)

    db.close()

    # Generar gráfico
    generate_graph(records)

    # Crear PDF
    pdf_path = "weekly_report.pdf"
    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, height - 50, "Weekly UV Exposure Report")

    c.setFont("Helvetica", 12)
    c.drawString(50, height - 100, f"Average UV Index: {round(avg_uv,2)}")
    c.drawString(50, height - 130, f"Maximum UV Index: {round(max_uv,2)}")
    c.drawString(50, height - 160, f"Total Dose: {round(total_dose,2)}")
    c.drawString(50, height - 190, f"Alarm Activations: {alarms}")

    # Insertar gráfico
    c.drawImage("uv_graph.png", 50, height - 500, width=500, height=250)

    c.save()

    # Enviar email
    send_email_with_attachment(pdf_path, recipient_email)

    return FileResponse(pdf_path, media_type='application/pdf',
                        filename="weekly_report.pdf")
