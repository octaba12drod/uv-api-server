from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Union, List
import os
import io
import base64
import json as json_lib
import urllib.request
 
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
 
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
 
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
 
# ============================================================
# App
# ============================================================
 
app = FastAPI(title="UV Monitor API")
 
# ============================================================
# Variables de entorno
# ============================================================
 
DATABASE_URL      = os.getenv("DATABASE_URL")
EMAIL_ADDRESS     = os.getenv("EMAIL_ADDRESS")
SENDGRID_API_KEY  = os.getenv("SENDGRID_API_KEY")
 
# ============================================================
# Base de datos
# ============================================================
 
engine       = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()
 
# ============================================================
# Modelos DB
# ============================================================
 
class UVData(Base):
    __tablename__ = "uv_data"
    id              = Column(Integer, primary_key=True, index=True)
    device_id       = Column(String, index=True)
    uv_index        = Column(Float)
    alarm_triggered = Column(Boolean)
    timestamp       = Column(DateTime, index=True)
 
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
 
Base.metadata.create_all(bind=engine)
 
# ============================================================
# Modelos Pydantic
# ============================================================
 
class UVRequest(BaseModel):
    device_id:       str
    uv_index:        float
    alarm_triggered: bool
    timestamp:       Union[str, int, datetime]
 
    @validator("timestamp", pre=True)
    def parse_timestamp(cls, v):
        if isinstance(v, datetime):
            return v
        if isinstance(v, (int, float)):
            return datetime.utcfromtimestamp(v / 1000)
        if isinstance(v, str):
            try:
                ms = int(v)
                return datetime.utcfromtimestamp(ms / 1000)
            except ValueError:
                return datetime.fromisoformat(v)
        raise ValueError(f"No se puede convertir timestamp: {v}")
 
class HistoryItem(BaseModel):
    date:             str
    max_uv:           float
    exposure_percent: float
    alarm_triggered:  bool
    total_sed:        float
 
class LecturaRequest(BaseModel):
    device_id:       str
    timestamp:       int
    uvi_promedio:    float
    uvi_maximo:      float
    dosis_intervalo: float
    dosis_acumulada: float
    hora:            int
    dia_semana:      int
 
# ============================================================
# Endpoints
# ============================================================
 
@app.get("/")
def root():
    return {"status": "ok", "app": "UV Monitor API"}
 
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
 
@app.get("/data")
def get_data(device_id: str = "ProtectorUV-01"):
    db = SessionLocal()
    records = db.query(UVData)\
        .filter(UVData.device_id == device_id)\
        .order_by(UVData.timestamp.desc())\
        .limit(50).all()
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
 
@app.get("/history", response_model=List[HistoryItem])
def get_history(device_id: str = "ProtectorUV-01", days: int = 7):
    db = SessionLocal()
    cutoff = datetime.utcnow() - timedelta(days=days)
    records = db.query(UVData)\
        .filter(UVData.device_id == device_id)\
        .filter(UVData.timestamp >= cutoff)\
        .order_by(UVData.timestamp.asc()).all()
    db.close()
 
    if not records:
        return []
 
    daily: dict = defaultdict(list)
    for r in records:
        daily[r.timestamp.date().isoformat()].append(r)
 
    result = []
    for date_str, day_records in sorted(daily.items()):
        max_uv  = max(r.uv_index for r in day_records)
        alarmed = any(r.alarm_triggered for r in day_records)
        total_sed = 0.0
        for i in range(len(day_records) - 1):
            uv = day_records[i].uv_index
            delta_hours = (
                day_records[i+1].timestamp - day_records[i].timestamp
            ).total_seconds() / 3600
            total_sed += uv * delta_hours * 0.9
        exposure_percent = min((total_sed / 3.0) * 100, 100.0)
        result.append(HistoryItem(
            date             = date_str,
            max_uv           = round(max_uv, 2),
            exposure_percent = round(exposure_percent, 1),
            alarm_triggered  = alarmed,
            total_sed        = round(total_sed, 3)
        ))
    return result
 
@app.post("/lecturas")
def receive_lectura(data: LecturaRequest):
    db = SessionLocal()
    try:
        ts = datetime.utcfromtimestamp(data.timestamp)
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
 
# ============================================================
# Helpers internos
# ============================================================
 
def _calculate_weekly_stats(records):
    if len(records) < 2:
        return None
 
    total_sed = 0.0
    max_uv    = 0.0
    alarms    = 0
 
    for i in range(len(records) - 1):
        uv = records[i].uv_index
        delta_hours = (
            records[i+1].timestamp - records[i].timestamp
        ).total_seconds() / 3600
        total_sed += uv * delta_hours * 0.9
        if uv > max_uv:
            max_uv = uv
        if records[i].alarm_triggered:
            alarms += 1
 
    avg_uv = sum(r.uv_index for r in records) / len(records)
 
    daily_sed = defaultdict(float)
    for i in range(len(records) - 1):
        day = records[i].timestamp.date().isoformat()
        uv  = records[i].uv_index
        dt  = (records[i+1].timestamp - records[i].timestamp).total_seconds() / 3600
        daily_sed[day] += uv * dt * 0.9
 
    high_days = sum(1 for sed in daily_sed.values() if sed > 3.0 * 0.7)
 
    return {
        "total_sed": round(total_sed, 3),
        "avg_uv":    round(avg_uv, 2),
        "max_uv":    round(max_uv, 2),
        "alarms":    alarms,
        "high_days": high_days,
        "daily_sed": dict(daily_sed),
    }
 
 
def _generate_graph(records) -> io.BytesIO:
    timestamps = [r.timestamp for r in records]
    uv_values  = [r.uv_index  for r in records]
 
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.fill_between(timestamps, uv_values, alpha=0.2, color="#FF9F0A")
    ax.plot(timestamps, uv_values, color="#FF9F0A", linewidth=2)
    ax.axhline(y=3, color="#34C759", linestyle="--", linewidth=1, label="Bajo (< 3)")
    ax.axhline(y=6, color="#FF9F0A", linestyle="--", linewidth=1, label="Moderado (3-6)")
    ax.axhline(y=8, color="#FF3B30", linestyle="--", linewidth=1, label="Alto (≥ 8)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    plt.xticks(rotation=30, ha="right")
    ax.set_xlabel("Fecha y hora", fontsize=11)
    ax.set_ylabel("Índice UV",    fontsize=11)
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
    pdf_buf = io.BytesIO()
    doc     = SimpleDocTemplate(pdf_buf, pagesize=letter,
                                topMargin=0.75*inch, bottomMargin=0.75*inch,
                                leftMargin=inch, rightMargin=inch)
    styles = getSampleStyleSheet()
    story  = []
 
    title_style = ParagraphStyle(
        "TitleStyle", parent=styles["Title"],
        fontSize=22, textColor=colors.HexColor("#1A1A2E"),
        spaceAfter=6, alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleStyle", parent=styles["Normal"],
        fontSize=11, textColor=colors.gray,
        spaceAfter=20, alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "SectionStyle", parent=styles["Heading2"],
        fontSize=13, textColor=colors.HexColor("#007AFF"),
        spaceBefore=14, spaceAfter=6,
    )
 
    now = datetime.now().strftime("%d/%m/%Y")
    story.append(Paragraph("☀️  Reporte Semanal de Exposición UV", title_style))
    story.append(Paragraph(f"Generado el {now} · Dispositivo: ProtectorUV-01", subtitle_style))
    story.append(Spacer(1, 0.1*inch))
 
    story.append(Paragraph("Resumen de la semana", section_style))
 
    risk_label = (
        "Seguro"    if stats["max_uv"] < 3 else
        "Moderado"  if stats["max_uv"] < 6 else
        "Alto"      if stats["max_uv"] < 8 else
        "Muy alto"
    )
 
    summary_data = [
        ["Métrica",                  "Valor"],
        ["UVI promedio",             str(stats["avg_uv"])],
        ["UVI máximo registrado",    f"{stats['max_uv']}  ({risk_label})"],
        ["Dosis total acumulada",    f"{stats['total_sed']} SED"],
        ["Activaciones de alarma",   f"{stats['alarms']}"],
        ["Días con exposición alta", f"{stats['high_days']} de 7"],
    ]
 
    table = Table(summary_data, colWidths=[3.2*inch, 3.2*inch])
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), colors.HexColor("#007AFF")),
        ("TEXTCOLOR",     (0,0),(-1,0), colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,0), 12),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("FONTNAME",      (0,1),(-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,1),(-1,-1), 11),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.HexColor("#F2F2F7"), colors.white]),
        ("GRID",          (0,0),(-1,-1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0,0),(-1,-1), 8),
        ("BOTTOMPADDING", (0,0),(-1,-1), 8),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.2*inch))
 
    story.append(Paragraph("Gráfica de exposición", section_style))
    story.append(Image(graph_buf, width=6*inch, height=2.8*inch))
    story.append(Spacer(1, 0.2*inch))
 
    story.append(Paragraph("Desglose por día", section_style))
    daily_data = [["Fecha", "Dosis (SED)", "% Tipo II", "Nivel"]]
    for date_str, sed in sorted(stats["daily_sed"].items()):
        pct   = min(round((sed / 3.0) * 100, 1), 100.0)
        level = (
            "Seguro"   if pct < 40  else
            "Moderado" if pct < 70  else
            "Alto"     if pct < 100 else
            "Crítico"
        )
        daily_data.append([date_str, str(round(sed, 3)), f"{pct}%", level])
 
    daily_table = Table(daily_data, colWidths=[1.8*inch, 1.6*inch, 1.5*inch, 1.5*inch])
    daily_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), colors.HexColor("#5856D6")),
        ("TEXTCOLOR",     (0,0),(-1,0), colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("FONTNAME",      (0,1),(-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,0),(-1,-1), 10),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.HexColor("#F2F2F7"), colors.white]),
        ("GRID",          (0,0),(-1,-1), 0.5, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
    ]))
    story.append(daily_table)
    story.append(Spacer(1, 0.2*inch))
 
    story.append(Paragraph("Recomendaciones", section_style))
    recomendaciones = []
    if stats["max_uv"] >= 8:
        recomendaciones.append("Se detectaron picos de UVI mayor o igual a 8. Usa protector solar FPS 50+ y evita exposición entre 11am y 3pm.")
    if stats["alarms"] > 0:
        recomendaciones.append(f"La alarma se activo {stats['alarms']} veces. Considera reducir el tiempo de exposicion directa.")
    if stats["high_days"] >= 3:
        recomendaciones.append(f"Hubo {stats['high_days']} dias con exposicion elevada. Mantén hábitos de protección consistentes.")
    if not recomendaciones:
        recomendaciones.append("Excelente semana. Tu exposicion UV se mantuvo dentro de niveles seguros.")
 
    for rec in recomendaciones:
        story.append(Paragraph(rec, styles["Normal"]))
        story.append(Spacer(1, 0.08*inch))
 
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(
        "<font size='9' color='grey'>ProtectorUV · Sistema de monitoreo UV personal · "
        "Valores SED calculados con base en irradiancia efectiva estandar (ISO 17166).</font>",
        styles["Normal"]
    ))
 
    doc.build(story)
    pdf_buf.seek(0)
    return pdf_buf
 
 
def _send_email(pdf_buf: io.BytesIO, recipient: str):
    """Envía el PDF usando SendGrid API (compatible con Railway)."""
    pdf_b64 = base64.b64encode(pdf_buf.read()).decode()
 
    payload = json_lib.dumps({
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": EMAIL_ADDRESS, "name": "UV Protect"},
        "subject": "Tu reporte semanal UV — ProtectorUV",
        "content": [{
            "type":  "text/plain",
            "value": "Adjunto encontraras tu reporte semanal de exposicion UV.\n\n— UV Protect"
        }],
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
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type":  "application/json"
        },
        method = "POST"
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 202):
            raise Exception(f"SendGrid error: {resp.status}")
 
 
# ============================================================
# Endpoint: reporte semanal
# ============================================================
 
@app.get("/weekly-report")
def weekly_report(recipient_email: str, device_id: str = "ProtectorUV-01"):
 
    db = SessionLocal()
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    records = db.query(UVData)\
        .filter(UVData.device_id == device_id)\
        .filter(UVData.timestamp >= one_week_ago)\
        .order_by(UVData.timestamp.asc()).all()
    db.close()
 
    if len(records) < 2:
        raise HTTPException(
            status_code=404,
            detail="No hay suficientes datos para generar el reporte (mínimo 2 registros)"
        )
 
    stats = _calculate_weekly_stats(records)
    if not stats:
        raise HTTPException(status_code=500, detail="Error calculando estadísticas")
 
    graph_buf = _generate_graph(records)
    pdf_buf   = _generate_pdf(stats, graph_buf)
 
    # ── Intentar enviar correo — si falla, igual devuelve el PDF ──
    pdf_buf.seek(0)
    email_status = "enviado correctamente"
    try:
        _send_email(pdf_buf, recipient_email)
    except Exception as e:
        email_status = f"no enviado: {str(e)[:80]}"
 
    # ── Siempre devolver el PDF como descarga ──────────────────
    pdf_buf.seek(0)
    return StreamingResponse(
        pdf_buf,
        media_type = "application/pdf",
        headers    = {
            "Content-Disposition": "attachment; filename=reporte_semanal_UV.pdf",
            "X-Email-Status":      email_status
        }
    )

