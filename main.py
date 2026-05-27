from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Union, List
import os
import io

from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

import matplotlib
matplotlib.use("Agg")   # backend sin pantalla (necesario en Railway)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import urllib.request
import json as json_lib

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

import smtplib
from email.message import EmailMessage


# ============================================================
# App
# ============================================================

app = FastAPI(title="UV Monitor API")


# ============================================================
# Variables de entorno (configúralas en Railway → Variables)
# ============================================================

DATABASE_URL  = os.getenv("DATABASE_URL")   # Railway lo da automáticamente con PostgreSQL
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")  # tu cuenta Gmail
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") # contraseña de aplicación Gmail


# ============================================================
# Base de datos
# ============================================================

engine       = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


# ============================================================
# Modelo DB
# ============================================================

class UVData(Base):
    __tablename__ = "uv_data"

    id              = Column(Integer, primary_key=True, index=True)
    device_id       = Column(String, index=True)
    uv_index        = Column(Float)
    alarm_triggered = Column(Boolean)
    timestamp       = Column(DateTime, index=True)


Base.metadata.create_all(bind=engine)


# ============================================================
# Modelo de entrada (con parseo de timestamp flexible)
#
# Android envía: timestamp = System.currentTimeMillis().toString()
# Ejemplo:       "1742940000000"  (milisegundos como string)
# El validator lo convierte a datetime automáticamente.
# ============================================================

class UVRequest(BaseModel):

    device_id:       str
    uv_index:        float
    alarm_triggered: bool
    timestamp:       Union[str, int, datetime]

    @validator("timestamp", pre=True)
    def parse_timestamp(cls, v):
        # Caso 1: ya es un datetime (FastAPI lo pasó directo)
        if isinstance(v, datetime):
            return v
        # Caso 2: número entero o float → unix milisegundos
        if isinstance(v, (int, float)):
            return datetime.utcfromtimestamp(v / 1000)
        # Caso 3: string numérico (lo que manda Android)
        if isinstance(v, str):
            try:
                ms = int(v)
                return datetime.utcfromtimestamp(ms / 1000)
            except ValueError:
                # Intenta parsear como ISO 8601 como fallback
                return datetime.fromisoformat(v)
        raise ValueError(f"No se puede convertir timestamp: {v}")


# ============================================================
# Modelo de respuesta para /history
# ============================================================

class HistoryItem(BaseModel):
    date:             str
    max_uv:           float
    exposure_percent: float
    alarm_triggered:  bool
    total_sed:        float


# ============================================================
# Endpoint: guardar dato UV
# POST /data
# ============================================================

@app.post("/data")
def receive_data(data: UVRequest):

    db = SessionLocal()

    try:
        new_record = UVData(
            device_id       = data.device_id,
            uv_index        = data.uv_index,
            alarm_triggered = data.alarm_triggered,
            timestamp       = data.timestamp
        )
        db.add(new_record)
        db.commit()

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        db.close()

    return {"message": "Datos guardados correctamente"}


# ============================================================
# Endpoint: ver últimos 50 registros
# GET /data
# ============================================================

@app.get("/data")
def get_data(device_id: str = "ProtectorUV-01"):

    db = SessionLocal()

    records = db.query(UVData)\
        .filter(UVData.device_id == device_id)\
        .order_by(UVData.timestamp.desc())\
        .limit(50)\
        .all()

    db.close()

    return [
        {
            "id":              r.id,
            "device_id":       r.device_id,
            "uv_index":        r.uv_index,
            "alarm_triggered": r.alarm_triggered,
            "timestamp":       r.timestamp.isoformat()
        }
        for r in records
    ]


# ============================================================
# Endpoint: historial diario para la app Android
# GET /history?device_id=ProtectorUV-01&days=7
#
# Regresa un resumen por día: max UV, dosis acumulada (SED),
# porcentaje de exposición (Tipo II como referencia) y alarmas.
# ============================================================

@app.get("/history", response_model=List[HistoryItem])
def get_history(device_id: str = "ProtectorUV-01", days: int = 7):

    db = SessionLocal()

    cutoff = datetime.utcnow() - timedelta(days=days)

    records = db.query(UVData)\
        .filter(UVData.device_id == device_id)\
        .filter(UVData.timestamp >= cutoff)\
        .order_by(UVData.timestamp.asc())\
        .all()

    db.close()

    if not records:
        return []

    # ── Agrupar registros por día ───────────────────────────
    daily: dict = defaultdict(list)
    for r in records:
        day_key = r.timestamp.date().isoformat()   # "2025-03-25"
        daily[day_key].append(r)

    result = []

    for date_str, day_records in sorted(daily.items()):

        max_uv  = max(r.uv_index for r in day_records)
        alarmed = any(r.alarm_triggered for r in day_records)

        # ── Calcular dosis en SED (Standard Erythemal Dose) ─
        # Fórmula: dose_SED += UVI × Δt_horas × 0.9
        # (equivale a irradiancia efectiva ≈ UVI × 0.025 W/m²)
        total_sed = 0.0
        for i in range(len(day_records) - 1):
            uv          = day_records[i].uv_index
            delta_hours = (
                day_records[i + 1].timestamp - day_records[i].timestamp
            ).total_seconds() / 3600
            total_sed  += uv * delta_hours * 0.9

        # ── Porcentaje respecto a Tipo II (3 SED = límite) ──
        # La app Android ajusta esto según el tipo de piel del usuario.
        exposure_percent = min((total_sed / 3.0) * 100, 100.0)

        result.append(HistoryItem(
            date             = date_str,
            max_uv           = round(max_uv, 2),
            exposure_percent = round(exposure_percent, 1),
            alarm_triggered  = alarmed,
            total_sed        = round(total_sed, 3)
        ))

    return result


# ============================================================
# Helpers internos
# ============================================================

def _calculate_weekly_stats(records):
    """Calcula estadísticas semanales a partir de una lista de registros."""

    if len(records) < 2:
        return None

    total_sed = 0.0
    max_uv    = 0.0
    alarms    = 0

    for i in range(len(records) - 1):
        uv          = records[i].uv_index
        delta_hours = (
            records[i + 1].timestamp - records[i].timestamp
        ).total_seconds() / 3600

        total_sed += uv * delta_hours * 0.9

        if uv > max_uv:
            max_uv = uv

        if records[i].alarm_triggered:
            alarms += 1

    avg_uv = sum(r.uv_index for r in records) / len(records)

    # Días con exposición alta (> 70% de 3 SED tipo II)
    daily_sed    = defaultdict(float)
    daily_points = defaultdict(list)
    for i in range(len(records) - 1):
        day = records[i].timestamp.date().isoformat()
        uv  = records[i].uv_index
        dt  = (records[i+1].timestamp - records[i].timestamp).total_seconds() / 3600
        daily_sed[day]    += uv * dt * 0.9
        daily_points[day].append(records[i])

    high_days = sum(1 for sed in daily_sed.values() if sed > 3.0 * 0.7)

    return {
        "total_sed":  round(total_sed, 3),
        "avg_uv":     round(avg_uv, 2),
        "max_uv":     round(max_uv, 2),
        "alarms":     alarms,
        "high_days":  high_days,
        "daily_sed":  dict(daily_sed),
    }


def _generate_graph(records) -> io.BytesIO:
    """
    Genera la gráfica de UV Index vs tiempo en memoria (BytesIO).
    No guarda archivos en disco — compatible con Railway.
    """

    timestamps = [r.timestamp for r in records]
    uv_values  = [r.uv_index  for r in records]

    fig, ax = plt.subplots(figsize=(10, 4))

    ax.fill_between(timestamps, uv_values, alpha=0.2, color="#FF9F0A")
    ax.plot(timestamps, uv_values, color="#FF9F0A", linewidth=2)

    # Líneas de referencia de riesgo
    ax.axhline(y=3,  color="#34C759", linestyle="--", linewidth=1, label="Bajo  (< 3)")
    ax.axhline(y=6,  color="#FF9F0A", linestyle="--", linewidth=1, label="Moderado (3-6)")
    ax.axhline(y=8,  color="#FF3B30", linestyle="--", linewidth=1, label="Alto  (≥ 8)")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    plt.xticks(rotation=30, ha="right")

    ax.set_xlabel("Fecha y hora",   fontsize=11)
    ax.set_ylabel("Índice UV",      fontsize=11)
    ax.set_title("Exposición UV — Últimos 7 días", fontsize=13, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def _generate_pdf(stats: dict, graph_buf: io.BytesIO) -> io.BytesIO:
    """
    Genera el PDF del reporte semanal en memoria (BytesIO).
    """

    pdf_buf = io.BytesIO()
    doc     = SimpleDocTemplate(pdf_buf, pagesize=letter,
                                topMargin=0.75*inch, bottomMargin=0.75*inch,
                                leftMargin=inch, rightMargin=inch)

    styles  = getSampleStyleSheet()
    story   = []

    # ── Estilos personalizados ──────────────────────────────
    title_style = ParagraphStyle(
        "TitleStyle",
        parent    = styles["Title"],
        fontSize  = 22,
        textColor = colors.HexColor("#1A1A2E"),
        spaceAfter= 6,
        alignment = TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleStyle",
        parent    = styles["Normal"],
        fontSize  = 11,
        textColor = colors.gray,
        spaceAfter= 20,
        alignment = TA_CENTER,
    )
    section_style = ParagraphStyle(
        "SectionStyle",
        parent    = styles["Heading2"],
        fontSize  = 13,
        textColor = colors.HexColor("#007AFF"),
        spaceBefore = 14,
        spaceAfter  = 6,
    )

    now = datetime.now().strftime("%d/%m/%Y")

    # ── Encabezado ──────────────────────────────────────────
    story.append(Paragraph("☀️  Reporte Semanal de Exposición UV", title_style))
    story.append(Paragraph(f"Generado el {now} · Dispositivo: ProtectorUV-01", subtitle_style))
    story.append(Spacer(1, 0.1*inch))

    # ── Tabla de resumen ────────────────────────────────────
    story.append(Paragraph("Resumen de la semana", section_style))

    risk_label = (
        "🟢 Seguro"     if stats["max_uv"] < 3 else
        "🟡 Moderado"   if stats["max_uv"] < 6 else
        "🔴 Alto"       if stats["max_uv"] < 8 else
        "🟣 Muy alto"
    )

    summary_data = [
        ["Métrica",               "Valor"],
        ["UVI promedio",          str(stats["avg_uv"])],
        ["UVI máximo registrado", f"{stats['max_uv']}  ({risk_label})"],
        ["Dosis total acumulada", f"{stats['total_sed']} SED"],
        ["Activaciones de alarma",f"{stats['alarms']}"],
        ["Días con exposición alta", f"{stats['high_days']} de 7"],
    ]

    table = Table(summary_data, colWidths=[3.2*inch, 3.2*inch])
    table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#007AFF")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 12),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 1), (-1, -1), 11),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.HexColor("#F2F2F7"), colors.white]),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4]),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.2*inch))

    # ── Gráfica ─────────────────────────────────────────────
    story.append(Paragraph("Gráfica de exposición", section_style))
    story.append(Image(graph_buf, width=6*inch, height=2.8*inch))
    story.append(Spacer(1, 0.2*inch))

    # ── Desglose diario ─────────────────────────────────────
    story.append(Paragraph("Desglose por día", section_style))

    daily_data = [["Fecha", "Dosis (SED)", "% Tipo II", "Nivel"]]
    for date_str, sed in sorted(stats["daily_sed"].items()):
        pct   = min(round((sed / 3.0) * 100, 1), 100.0)
        level = (
            "Seguro"    if pct < 40  else
            "Moderado"  if pct < 70  else
            "Alto"      if pct < 100 else
            "Crítico"
        )
        daily_data.append([date_str, str(round(sed, 3)), f"{pct}%", level])

    daily_table = Table(daily_data, colWidths=[1.8*inch, 1.6*inch, 1.5*inch, 1.5*inch])
    daily_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#5856D6")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.HexColor("#F2F2F7"), colors.white]),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(daily_table)
    story.append(Spacer(1, 0.2*inch))

    # ── Recomendaciones ─────────────────────────────────────
    story.append(Paragraph("Recomendaciones", section_style))

    recomendaciones = []
    if stats["max_uv"] >= 8:
        recomendaciones.append("⚠️  Se detectaron picos de UVI ≥ 8. Usa protector solar FPS 50+ y evita exposición entre 11am–3pm.")
    if stats["alarms"] > 0:
        recomendaciones.append(f"🔔  La alarma se activó {stats['alarms']} veces. Considera reducir el tiempo de exposición directa.")
    if stats["high_days"] >= 3:
        recomendaciones.append(f"📅  Hubo {stats['high_days']} días con exposición elevada. Mantén hábitos de protección consistentes.")
    if not recomendaciones:
        recomendaciones.append("✅  ¡Excelente semana! Tu exposición UV se mantuvo dentro de niveles seguros.")

    for rec in recomendaciones:
        story.append(Paragraph(rec, styles["Normal"]))
        story.append(Spacer(1, 0.08*inch))

    # ── Pie de página ────────────────────────────────────────
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(
        "<font size='9' color='grey'>ProtectorUV · Sistema de monitoreo UV personal · "
        "Los valores SED se calculan con base en la irradiancia efectiva estándar (ISO 17166).</font>",
        styles["Normal"]
    ))

    doc.build(story)
    pdf_buf.seek(0)
    return pdf_buf


def _send_email(pdf_buf: io.BytesIO, recipient: str):
    import base64
    pdf_b64 = base64.b64encode(pdf_buf.read()).decode()

    payload = json_lib.dumps({
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": EMAIL_ADDRESS, "name": "UV Protect"},
        "subject": "☀️ Tu reporte semanal UV — ProtectorUV",
        "content": [{"type": "text/plain", "value":
            "Adjunto encontrarás tu reporte semanal de exposición UV.\n\n— UV Protect"}],
        "attachments": [{
            "content":     pdf_b64,
            "type":        "application/pdf",
            "filename":    "reporte_semanal_UV.pdf",
            "disposition": "attachment"
        }]
    }).encode()

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data    = payload,
        headers = {
            "Authorization": f"Bearer {os.getenv('SENDGRID_API_KEY')}",
            "Content-Type":  "application/json"
        },
        method = "POST"
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 202):
            raise Exception(f"SendGrid error: {resp.status}")


# ============================================================
# Endpoint: reporte semanal
# GET /weekly-report?recipient_email=user@gmail.com
#
# Genera un PDF con análisis completo y lo envía por correo.
# También lo regresa como descarga directa.
# ============================================================

@app.get("/weekly-report")
def weekly_report(recipient_email: str, device_id: str = "ProtectorUV-01"):

    db = SessionLocal()

    one_week_ago = datetime.utcnow() - timedelta(days=7)

    records = db.query(UVData)\
        .filter(UVData.device_id == device_id)\
        .filter(UVData.timestamp >= one_week_ago)\
        .order_by(UVData.timestamp.asc())\
        .all()

    db.close()

    if len(records) < 2:
        raise HTTPException(
            status_code = 404,
            detail      = "No hay suficientes datos para generar el reporte (mínimo 2 registros)"
        )

    # Calcular estadísticas
    stats = _calculate_weekly_stats(records)
    if not stats:
        raise HTTPException(status_code=500, detail="Error calculando estadísticas")

    # Generar gráfica en memoria
    graph_buf = _generate_graph(records)

    # Generar PDF en memoria
    pdf_buf = _generate_pdf(stats, graph_buf)

    # Enviar por correo
    pdf_buf.seek(0)
    try:
        _send_email(pdf_buf, recipient_email)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error enviando correo: {e}")

    # También devolver el PDF como descarga
    pdf_buf.seek(0)
    return StreamingResponse(
        pdf_buf,
        media_type = "application/pdf",
        headers    = {"Content-Disposition": "attachment; filename=reporte_semanal_UV.pdf"}
    )


# ============================================================
# Healthcheck
# ============================================================

@app.get("/")
def root():
    return {"status": "ok", "app": "UV Monitor API"}


# ============================================================
# Modelo DB — tabla lecturas (intervalos de 15 min)
# ============================================================

class Lectura(Base):
    __tablename__ = "lecturas"

    id              = Column(Integer, primary_key=True, index=True)
    device_id       = Column(String, index=True)
    timestamp       = Column(DateTime, index=True)
    hora            = Column(Integer)
    dia_semana      = Column(Integer)
    uvi_promedio    = Column(Float)
    uvi_maximo      = Column(Float)
    dosis_intervalo = Column(Float)
    dosis_acumulada = Column(Float)

    __table_args__ = (
        # UPSERT: si llega el mismo timestamp + device_id se ignora
        # Esto hace seguro reenviar sin duplicar datos
        {"sqlite_autoincrement": False},
    )


Base.metadata.create_all(bind=engine)


class LecturaRequest(BaseModel):
    device_id:       str
    timestamp:       int        # Unix time en segundos
    uvi_promedio:    float
    uvi_maximo:      float
    dosis_intervalo: float      # J/m²
    dosis_acumulada: float      # J/m²
    hora:            int
    dia_semana:      int


# ============================================================
# Endpoint: recibir registro de intervalo de 15 min
# POST /lecturas
# Usa INSERT ... ON CONFLICT DO NOTHING (UPSERT seguro)
# ============================================================

@app.post("/lecturas")
def receive_lectura(data: LecturaRequest):

    db = SessionLocal()

    try:
        ts = datetime.utcfromtimestamp(data.timestamp)

        # Verificar si ya existe este registro (UPSERT manual)
        existe = db.query(Lectura).filter(
            Lectura.device_id == data.device_id,
            Lectura.timestamp == ts
        ).first()

        if not existe:
            nueva = Lectura(
                device_id       = data.device_id,
                timestamp       = ts,
                hora            = data.hora,
                dia_semana      = data.dia_semana,
                uvi_promedio    = data.uvi_promedio,
                uvi_maximo      = data.uvi_maximo,
                dosis_intervalo = data.dosis_intervalo,
                dosis_acumulada = data.dosis_acumulada
            )
            db.add(nueva)
            db.commit()
            return {"message": "Lectura guardada"}
        else:
            return {"message": "Lectura ya existía — ignorada (UPSERT)"}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
