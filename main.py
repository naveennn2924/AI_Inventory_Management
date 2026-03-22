"""
Rooh Gastronomy – AI Inventory Management System v2
----------------------------------------------------
Features:
  • Bill image → OCR → auto stock update (POST /bills/scan)
  • Telegram bot usage logging (POST /telegram/usage)
  • Live dashboard with urgent/reorder/ok status (GET /dashboard)
  • Full CRUD + NL AI queries
  • Background low-stock + expiry checker every 30 min

Run:   uvicorn main:app --reload --port 8001
Docs:  http://localhost:8001/docs
"""

import os, json, base64, re
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
from dotenv import load_dotenv

from models import create_tables, get_db, InventoryItem, StockMovement, BillScan, Alert, QueryLog
from seed import seed

load_dotenv()

app = FastAPI(title="Rooh Gastronomy – AI Inventory v2", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    name: str
    category: str
    department: str = "kitchen"     # kitchen | bar | extras
    storage: Optional[str] = None
    unit: str
    current_stock: float
    reorder_level: Optional[float] = None
    critical_level: Optional[float] = None
    expiry_date: Optional[str] = None
    supplier: Optional[str] = None
    sub_category: Optional[str] = None

class ItemUpdate(BaseModel):
    current_stock: Optional[float] = None
    reorder_level: Optional[float] = None
    critical_level: Optional[float] = None
    expiry_date: Optional[str] = None
    supplier: Optional[str] = None
    quality: Optional[str] = None

class UsageLog(BaseModel):
    item_name: str
    quantity: float
    unit: str
    entered_by: str
    remarks: Optional[str] = None

class NLQuery(BaseModel):
    question: str

class TelegramUsage(BaseModel):
    """What the Telegram bot sends when a staff member logs usage."""
    user: str
    items: List[UsageLog]

# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    create_tables()
    seed()
    _start_scheduler()

def _start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(_check_alerts, "interval", minutes=30)
    scheduler.start()

# ── Alert engine ──────────────────────────────────────────────────────────────

def _check_alerts():
    db = next(get_db())
    items = db.query(InventoryItem).all()
    today = date.today()

    for item in items:
        # Critical stock
        if item.critical_level and item.current_stock <= item.critical_level:
            _upsert_alert(db, item.name, "critical",
                f"🔴 CRITICAL: {item.name} is at {item.current_stock} {item.unit} (critical level: {item.critical_level})")
        # Reorder
        elif item.reorder_level and item.current_stock <= item.reorder_level:
            _upsert_alert(db, item.name, "reorder",
                f"🟡 REORDER: {item.name} is at {item.current_stock} {item.unit} (reorder level: {item.reorder_level})")

        # Expiry checks
        if item.expiry_date:
            try:
                exp = datetime.strptime(item.expiry_date, "%d.%m.%Y").date()
                days_left = (exp - today).days
                if days_left < 0:
                    _upsert_alert(db, item.name, "expired",
                        f"⛔ EXPIRED: {item.name} expired on {item.expiry_date}")
                elif days_left <= 7:
                    _upsert_alert(db, item.name, "expiring_soon",
                        f"⚠️ EXPIRING SOON: {item.name} expires in {days_left} days ({item.expiry_date})")
            except Exception:
                pass
    db.commit()

def _upsert_alert(db, item_name, alert_type, message):
    existing = db.query(Alert).filter(
        Alert.item_name == item_name,
        Alert.alert_type == alert_type,
        Alert.resolved == 0
    ).first()
    if not existing:
        db.add(Alert(item_name=item_name, alert_type=alert_type, message=message))

# ── CRUD endpoints ────────────────────────────────────────────────────────────

@app.get("/items", summary="List all inventory items")
def list_items(department: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(InventoryItem)
    if department:
        q = q.filter(InventoryItem.department == department)
    return [_item_dict(i) for i in q.all()]

@app.post("/items", summary="Add new inventory item")
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    if db.query(InventoryItem).filter(InventoryItem.name == item.name).first():
        raise HTTPException(400, f"'{item.name}' already exists")
    db.add(InventoryItem(**item.model_dump()))
    db.commit()
    return {"message": "Added", "name": item.name}

@app.patch("/items/{item_id}", summary="Update item")
def update_item(item_id: int, update: ItemUpdate, updated_by: str = "api", db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    for field, val in update.model_dump(exclude_none=True).items():
        setattr(item, field, val)
    item.updated_by = updated_by
    item.last_updated = datetime.utcnow()
    db.commit()
    _check_alerts()
    return {"message": "Updated", "item": item.name}

@app.delete("/items/{item_id}", summary="Remove item")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "Item not found")
    db.delete(item)
    db.commit()
    return {"message": f"'{item.name}' removed"}

# ── Stock movement ────────────────────────────────────────────────────────────

@app.post("/stock/use", summary="Log usage (OUT) — same as Telegram bot")
def log_usage(usage: UsageLog, db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.name == usage.item_name).first()
    if not item:
        raise HTTPException(404, f"'{usage.item_name}' not in inventory")
    if item.current_stock < usage.quantity:
        raise HTTPException(400, f"Only {item.current_stock} {item.unit} available")
    item.current_stock -= usage.quantity
    item.updated_by = usage.entered_by
    item.last_updated = datetime.utcnow()
    db.add(StockMovement(
        item_name=usage.item_name, direction="OUT",
        quantity=usage.quantity, unit=usage.unit,
        source="api", entered_by=usage.entered_by, remarks=usage.remarks
    ))
    db.commit()
    _check_alerts()
    return {"message": f"Used {usage.quantity} {usage.unit} of {usage.item_name}",
            "remaining": item.current_stock}

@app.post("/stock/add", summary="Manually add stock (IN)")
def add_stock(item_name: str, quantity: float, unit: str, entered_by: str = "manual",
              remarks: Optional[str] = None, db: Session = Depends(get_db)):
    item = db.query(InventoryItem).filter(InventoryItem.name == item_name).first()
    if not item:
        raise HTTPException(404, f"'{item_name}' not in inventory")
    item.current_stock += quantity
    item.updated_by = entered_by
    item.last_updated = datetime.utcnow()
    db.add(StockMovement(
        item_name=item_name, direction="IN",
        quantity=quantity, unit=unit,
        source="manual", entered_by=entered_by, remarks=remarks
    ))
    db.commit()
    return {"message": f"Added {quantity} {unit} to {item_name}", "new_stock": item.current_stock}

# ── BILL SCAN (image → OCR → auto stock IN) ──────────────────────────────────

@app.post("/bills/scan", summary="Upload bill image → AI extracts items → updates stock")
async def scan_bill(
    file: UploadFile = File(...),
    uploaded_by: str = Form(default="manager"),
    auto_apply: str = Form(default="false"),   # Form sends strings, not bool
    db: Session = Depends(get_db)
):
    """
    Upload a photo of a supplier bill/invoice.
    AI extracts item names, quantities, and units using GPT-4o vision.
    Returns parsed items for review. Set auto_apply=true to immediately update stock.

    Supported image formats: JPG, PNG, WEBP, GIF
    """
    # Read file
    try:
        image_data = await file.read()
    except Exception as e:
        raise HTTPException(400, f"Could not read uploaded file: {e}")

    if not image_data:
        raise HTTPException(400, "Uploaded file is empty")

    # Detect MIME type — Forms sometimes send wrong content_type
    mime = file.content_type or "image/jpeg"
    fname = (file.filename or "").lower()
    if fname.endswith(".png"):
        mime = "image/png"
    elif fname.endswith(".jpg") or fname.endswith(".jpeg"):
        mime = "image/jpeg"
    elif fname.endswith(".webp"):
        mime = "image/webp"
    elif fname.endswith(".gif"):
        mime = "image/gif"
    # Ensure mime is a valid image type
    if not mime.startswith("image/"):
        mime = "image/jpeg"

    b64 = base64.b64encode(image_data).decode("utf-8")
    should_apply = str(auto_apply).lower() in ("true", "1", "yes")

    # GPT-4o Vision: extract items from bill
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are an AI assistant for Rooh Gastronomy restaurant inventory system in Germany.\n"
                            "Extract all purchased items from this supplier bill/invoice image.\n\n"
                            "Return ONLY valid JSON — no markdown, no explanation:\n"
                            "{\n"
                            '  "supplier": "supplier name or Unknown",\n'
                            '  "date": "DD.MM.YYYY or today",\n'
                            '  "items": [\n'
                            '    {"name": "item name", "quantity": 2.5, "unit": "Kg"},\n'
                            "    ...\n"
                            "  ]\n"
                            "}\n\n"
                            "Rules:\n"
                            "- Include every line item\n"
                            "- Normalise units: Kg, Ltr, Pieces, Gms, Ml, Pack, Bottle\n"
                            "- Convert German: Stk→Pieces, Flasche→Bottle, kg→Kg, l→Ltr\n"
                            "- quantity must be a number"
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64}",
                            "detail": "high"
                        }
                    }
                ]
            }],
            max_tokens=1000
        )
    except Exception as e:
        raise HTTPException(500, f"OpenAI Vision API error: {str(e)}. Check your OPENAI_API_KEY.")

    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
    except Exception:
        # Still save the raw result so it's not lost
        scan = BillScan(raw_text=raw, parsed_items="[]", uploaded_by=uploaded_by)
        db.add(scan)
        db.commit()
        return {
            "error": "AI could not produce valid JSON from this image",
            "tip": "Try a clearer, better-lit photo of the invoice",
            "raw_ai_response": raw[:500]
        }

    items = parsed.get("items", [])
    if not items:
        return {
            "warning": "No line items found in the bill",
            "supplier": parsed.get("supplier"),
            "tip": "Make sure the image shows item names and quantities clearly"
        }

    # Save scan record
    scan = BillScan(
        raw_text=raw,
        parsed_items=json.dumps(items),
        uploaded_by=uploaded_by
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    applied_count = 0
    not_found = []

    if should_apply:
        for bill_item in items:
            name = bill_item.get("name", "")
            try:
                qty = float(bill_item.get("quantity", 0))
            except (ValueError, TypeError):
                qty = 0.0
            unit = bill_item.get("unit", "")

            inv_item = _find_item(db, name)
            if inv_item and qty > 0:
                inv_item.current_stock += qty
                inv_item.last_updated = datetime.utcnow()
                inv_item.updated_by = uploaded_by
                db.add(StockMovement(
                    item_name=inv_item.name, direction="IN",
                    quantity=qty, unit=unit,
                    source="bill_scan", entered_by=uploaded_by,
                    remarks=f"Bill #{scan.id} – {parsed.get('supplier', 'Unknown supplier')}"
                ))
                applied_count += 1
            else:
                not_found.append(name)

        scan.applied = 1
        db.commit()
        _check_alerts()

    return {
        "scan_id": scan.id,
        "supplier": parsed.get("supplier", "Unknown"),
        "date": parsed.get("date", ""),
        "items_found": len(items),
        "items": items,
        "auto_applied": should_apply,
        "applied_count": applied_count if should_apply else 0,
        "not_found_in_inventory": not_found,
        "next_step": None if should_apply else f"POST /bills/{scan.id}/apply to apply to stock after review"
    }


@app.post("/bills/{scan_id}/apply", summary="Apply a scanned bill to stock")
def apply_bill(scan_id: int, applied_by: str = "manager", db: Session = Depends(get_db)):
    scan = db.query(BillScan).filter(BillScan.id == scan_id).first()
    if not scan:
        raise HTTPException(404, "Scan not found")
    if scan.applied:
        return {"message": "Already applied"}
    items = json.loads(scan.parsed_items)
    applied, not_found = [], []
    for bill_item in items:
        name = bill_item.get("name", "")
        qty = float(bill_item.get("quantity", 0))
        unit = bill_item.get("unit", "")
        inv_item = _find_item(db, name)
        if inv_item:
            inv_item.current_stock += qty
            inv_item.last_updated = datetime.utcnow()
            inv_item.updated_by = applied_by
            db.add(StockMovement(
                item_name=inv_item.name, direction="IN",
                quantity=qty, unit=unit,
                source="bill_scan", entered_by=applied_by,
                remarks=f"Bill scan #{scan_id}"
            ))
            applied.append(inv_item.name)
        else:
            not_found.append(name)
    scan.applied = 1
    db.commit()
    _check_alerts()
    return {"applied": applied, "not_found": not_found}


@app.get("/bills", summary="List all bill scans")
def list_bills(db: Session = Depends(get_db)):
    scans = db.query(BillScan).order_by(BillScan.created_at.desc()).all()
    return [{"id": s.id, "uploaded_by": s.uploaded_by, "applied": bool(s.applied),
             "items_count": len(json.loads(s.parsed_items)),
             "created_at": s.created_at.isoformat()} for s in scans]

# ── TELEGRAM BOT ENDPOINT ─────────────────────────────────────────────────────

@app.post("/telegram/usage", summary="Telegram bot posts usage (staff logs what they took)")
def telegram_usage(payload: TelegramUsage, db: Session = Depends(get_db)):
    """
    The Telegram bot sends this when a staff member types something like:
    /use chicken 2 kg
    /use onion 3 kg tomato 0.5 kg
    """
    results = []
    for u in payload.items:
        item = _find_item(db, u.item_name)
        if item:
            if item.current_stock >= u.quantity:
                item.current_stock -= u.quantity
                item.updated_by = payload.user
                item.last_updated = datetime.utcnow()
                db.add(StockMovement(
                    item_name=item.name, direction="OUT",
                    quantity=u.quantity, unit=u.unit,
                    source="telegram", entered_by=payload.user, remarks=u.remarks
                ))
                results.append({"item": item.name, "status": "ok",
                                 "remaining": item.current_stock, "unit": item.unit})
            else:
                results.append({"item": item.name, "status": "insufficient",
                                 "available": item.current_stock, "unit": item.unit})
        else:
            results.append({"item": u.item_name, "status": "not_found"})
    db.commit()
    _check_alerts()
    return {"user": payload.user, "results": results}

# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@app.get("/dashboard", summary="Full inventory dashboard with status breakdown")
def dashboard(db: Session = Depends(get_db)):
    """
    Returns structured dashboard data:
    - urgent: critical stock or expired
    - order_soon: below reorder level
    - expiring_soon: within 7 days
    - ok: all fine
    - stats: counts per department
    """
    _check_alerts()
    items = db.query(InventoryItem).all()
    today = date.today()

    urgent, order_soon, expiring_soon, ok = [], [], [], []

    for i in items:
        d = _item_dict(i)
        days_to_expiry = None
        if i.expiry_date:
            try:
                exp = datetime.strptime(i.expiry_date, "%d.%m.%Y").date()
                days_to_expiry = (exp - today).days
                d["days_to_expiry"] = days_to_expiry
            except Exception:
                pass

        is_critical = i.critical_level and i.current_stock <= i.critical_level
        is_expired  = days_to_expiry is not None and days_to_expiry < 0
        is_reorder  = i.reorder_level and i.current_stock <= i.reorder_level and not is_critical
        is_expiring = days_to_expiry is not None and 0 <= days_to_expiry <= 7

        if is_critical or is_expired:
            d["alert"] = "🔴 URGENT"
            urgent.append(d)
        elif is_reorder or is_expiring:
            d["alert"] = "🟡 ORDER SOON" if is_reorder else "⚠️ EXPIRING SOON"
            order_soon.append(d)
        else:
            d["alert"] = "✅ OK"
            ok.append(d)

    active_alerts = db.query(Alert).filter(Alert.resolved == 0).all()

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "total_items": len(items),
            "urgent_count": len(urgent),
            "order_soon_count": len(order_soon),
            "ok_count": len(ok),
            "active_alerts": len(active_alerts),
        },
        "urgent": urgent,
        "order_soon": order_soon,
        "ok": ok,
        "active_alerts": [
            {"id": a.id, "item": a.item_name, "type": a.alert_type,
             "message": a.message, "since": a.created_at.isoformat()}
            for a in active_alerts
        ]
    }


@app.get("/dashboard/ui", response_class=HTMLResponse, summary="Production dashboard — live data from API")
def dashboard_ui(db: Session = Depends(get_db)):
    """
    Full production dashboard. All data is fetched live from the API via JS fetch()
    on page load and every 60 seconds. No page reload needed.
    Open: http://localhost:8001/dashboard/ui
    """
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rooh Gastronomy – Inventory Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --ink:#0f1117;--ink2:#3a3d4a;--ink3:#6b6f7e;--ink4:#9ca0b0;
  --surface:#ffffff;--surface2:#f4f5f8;--surface3:#eceef3;
  --red:#d63b3b;--red-bg:#fff0f0;--red-bd:#f5c0c0;
  --amber:#c47c00;--amber-bg:#fffbec;--amber-bd:#f5d87a;
  --green:#1a7a4a;--green-bg:#edfaf3;--green-bd:#94d9b4;
  --blue:#1860cc;--blue-bg:#eef4ff;--blue-bd:#a8c4f5;
  --border:#e2e4ec;--radius:10px;--radius-lg:14px;
}
body{font-family:'DM Sans',sans-serif;background:var(--surface2);color:var(--ink);font-size:13px;line-height:1.5;min-height:100vh}
.shell{display:grid;grid-template-columns:200px 1fr;min-height:100vh}

/* ── Sidebar ── */
.nav{background:var(--ink);display:flex;flex-direction:column;position:sticky;top:0;height:100vh;overflow-y:auto}
.nav-logo{padding:20px 18px 16px;border-bottom:1px solid rgba(255,255,255,.07)}
.nav-brand{color:#fff;font-size:15px;font-weight:600;letter-spacing:-.3px}
.nav-sub{color:rgba(255,255,255,.35);font-size:11px;margin-top:2px}
.nav-section{padding:14px 10px 4px;color:rgba(255,255,255,.3);font-size:10px;font-weight:500;letter-spacing:.8px;text-transform:uppercase}
.nav-item{display:flex;align-items:center;gap:9px;padding:8px 12px;border-radius:7px;margin:1px 8px;color:rgba(255,255,255,.55);font-size:12.5px;cursor:pointer;transition:all .15s;text-decoration:none}
.nav-item:hover{background:rgba(255,255,255,.07);color:rgba(255,255,255,.9)}
.nav-item.active{background:rgba(255,255,255,.1);color:#fff}
.nav-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.dot-red{background:#f27474}.dot-amber{background:#f5c842}.dot-green{background:#5cd68e}.dot-blue{background:#70a4f5}
.nav-badge{margin-left:auto;background:rgba(255,255,255,.12);color:rgba(255,255,255,.7);font-size:10px;padding:1px 6px;border-radius:10px;min-width:18px;text-align:center}
.nav-footer{margin-top:auto;padding:14px 10px;border-top:1px solid rgba(255,255,255,.07)}
.avatar{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#5b8af5,#9b5cf5);display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:600;flex-shrink:0}
.nav-user{display:flex;align-items:center;gap:9px}
.nav-user-name{color:rgba(255,255,255,.7);font-size:12px}
.nav-user-role{color:rgba(255,255,255,.3);font-size:10px}

/* ── Main ── */
.main{padding:24px;overflow:auto;max-width:1400px}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:22px}
.page-title{font-size:20px;font-weight:600;letter-spacing:-.4px}
.page-sub{color:var(--ink3);font-size:12px;margin-top:2px}
.topbar-right{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:7px;font-size:12.5px;font-weight:500;cursor:pointer;border:none;font-family:inherit;transition:all .15s;text-decoration:none}
.btn-ghost{background:var(--surface);border:1px solid var(--border);color:var(--ink2)}
.btn-ghost:hover{background:var(--surface3)}
.btn-primary{background:var(--ink);color:#fff}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red-bg);border:1px solid var(--red-bd);color:var(--red)}
.time-badge{background:var(--surface);border:1px solid var(--border);border-radius:7px;padding:6px 12px;font-size:12px;color:var(--ink3);font-family:'DM Mono',monospace}
.sync-dot{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* ── KPI row ── */
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:22px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px 18px;position:relative;overflow:hidden;cursor:pointer;transition:border-color .2s,transform .15s}
.kpi:hover{border-color:var(--ink4);transform:translateY(-1px)}
.kpi-accent{position:absolute;top:0;left:0;right:0;height:3px}
.kpi-label{font-size:11.5px;color:var(--ink3);font-weight:500;letter-spacing:.1px;margin-bottom:8px}
.kpi-val{font-size:32px;font-weight:600;letter-spacing:-.8px;line-height:1;transition:all .3s}
.kpi-val.red{color:var(--red)}.kpi-val.amber{color:var(--amber)}.kpi-val.green{color:var(--green)}.kpi-val.blue{color:var(--blue)}
.kpi-pill{display:inline-flex;align-items:center;font-size:10.5px;font-weight:500;padding:2px 7px;border-radius:10px;margin-top:8px}
.pill-red{background:var(--red-bg);color:var(--red)}.pill-amber{background:var(--amber-bg);color:var(--amber)}.pill-green{background:var(--green-bg);color:var(--green)}

/* ── Layouts ── */
.grid3{display:grid;grid-template-columns:1.5fr 1fr;gap:14px;margin-bottom:14px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.grid-right{display:flex;flex-direction:column;gap:14px}

/* ── Card ── */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden}
.card-head{padding:13px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:8px}
.card-title{font-size:13px;font-weight:600;color:var(--ink)}
.card-hint{font-size:11.5px;color:var(--ink4)}

/* ── Table ── */
.tbl-wrap{overflow-x:auto}
table.data{width:100%;border-collapse:collapse}
table.data th{font-size:10.5px;font-weight:500;color:var(--ink4);text-transform:uppercase;letter-spacing:.5px;padding:9px 18px;text-align:left;background:var(--surface2);border-bottom:1px solid var(--border);white-space:nowrap}
table.data td{padding:10px 18px;border-bottom:1px solid var(--surface2);vertical-align:middle;color:var(--ink2);font-size:12.5px}
table.data tr:last-child td{border-bottom:none}
table.data tr:hover td{background:var(--surface2)}
.item-name{font-weight:500;color:var(--ink)}
.item-sub{font-size:11px;color:var(--ink4);margin-top:1px}

/* ── Badges ── */
.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;padding:3px 9px;border-radius:10px;white-space:nowrap}
.b-critical{background:var(--red-bg);color:var(--red);border:1px solid var(--red-bd)}
.b-reorder{background:var(--amber-bg);color:var(--amber);border:1px solid var(--amber-bd)}
.b-expiring{background:#fff7ee;color:#b86000;border:1px solid #f0cc80}
.b-ok{background:var(--green-bg);color:var(--green);border:1px solid var(--green-bd)}
.b-dept{background:var(--surface2);color:var(--ink3);border:1px solid var(--border);font-size:10.5px;padding:2px 7px}

/* ── Progress bar ── */
.prog-wrap{width:70px}
.prog-bg{height:5px;background:var(--surface3);border-radius:10px;overflow:hidden}
.prog-fill{height:100%;border-radius:10px;transition:width .5s}
.pf-red{background:var(--red)}.pf-amber{background:#e6a800}.pf-green{background:#1a9e5c}

/* ── Stock value ── */
.sv{font-family:'DM Mono',monospace;font-size:12px}
.sv-red{color:var(--red)}.sv-amber{color:var(--amber)}.sv-ok{color:var(--ink3)}

/* ── Alerts ── */
.alert-list{padding:4px 0}
.alert-row{display:flex;align-items:flex-start;gap:11px;padding:10px 16px;border-bottom:1px solid var(--surface2);transition:background .15s}
.alert-row:last-child{border-bottom:none}
.alert-row:hover{background:var(--surface2)}
.al-icon{width:28px;height:28px;border-radius:7px;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:13px}
.ai-red{background:var(--red-bg)}.ai-amber{background:var(--amber-bg)}.ai-blue{background:var(--blue-bg)}
.al-body{flex:1;min-width:0}
.al-text{font-size:12px;color:var(--ink2);line-height:1.4}
.al-text b{font-weight:600;color:var(--ink)}
.al-meta{font-size:10.5px;color:var(--ink4);margin-top:2px}
.al-resolve{background:none;border:1px solid var(--border);border-radius:6px;padding:2px 9px;font-size:10.5px;cursor:pointer;color:var(--ink3);font-family:inherit;white-space:nowrap;flex-shrink:0}
.al-resolve:hover{background:var(--surface2)}

/* ── Expiry tracker ── */
.exp-row{display:flex;align-items:center;justify-content:space-between;padding:9px 18px;border-bottom:1px solid var(--surface2);gap:8px}
.exp-row:last-child{border-bottom:none}
.exp-days{font-size:11px;font-family:'DM Mono',monospace;padding:2px 8px;border-radius:8px;font-weight:500;white-space:nowrap}
.ed-expired{background:var(--red-bg);color:var(--red)}.ed-soon{background:var(--amber-bg);color:var(--amber)}.ed-ok{background:var(--surface3);color:var(--ink3)}

/* ── Movements ── */
.mv-row{display:flex;align-items:center;gap:10px;padding:9px 16px;border-bottom:1px solid var(--surface2);font-size:12px}
.mv-row:last-child{border-bottom:none}
.mv-dir{width:32px;height:22px;border-radius:5px;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;flex-shrink:0}
.mv-in{background:var(--green-bg);color:var(--green)}.mv-out{background:var(--red-bg);color:var(--red)}
.mv-item{font-weight:500;color:var(--ink);flex:1}
.mv-qty{font-family:'DM Mono',monospace;font-size:11.5px;color:var(--ink3)}
.mv-by{font-size:11px;color:var(--ink4)}
.mv-src{font-size:10px;padding:1px 6px;border-radius:8px;background:var(--surface2);color:var(--ink4)}

/* ── Bar chart ── */
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:9px}
.bar-label{font-size:11.5px;color:var(--ink3);width:110px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{flex:1;height:7px;background:var(--surface3);border-radius:6px;overflow:hidden}
.bar-fill{height:100%;border-radius:6px;transition:width .5s}
.bar-val{font-size:11px;font-family:'DM Mono',monospace;color:var(--ink3);width:36px;text-align:right;flex-shrink:0}

/* ── Dept cards ── */
.dept-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:14px 16px}
.dept-card{border:1px solid var(--border);border-radius:9px;padding:10px 12px;text-align:center}
.dept-num{font-size:22px;font-weight:600;color:var(--ink)}
.dept-name{font-size:11px;color:var(--ink3);margin-top:2px}
.dept-crit{font-size:10.5px;margin-top:5px;font-weight:500}

/* ── Bill scanner ── */
.scan-zone{margin:12px 16px 14px;border:1.5px dashed var(--border);border-radius:var(--radius);padding:18px;text-align:center;cursor:pointer;transition:all .2s}
.scan-zone:hover{border-color:var(--blue);background:var(--blue-bg)}
.scan-zone input[type=file]{display:none}
.scan-icon{font-size:22px;margin-bottom:6px}
.scan-title{font-size:12.5px;font-weight:500;color:var(--ink2)}
.scan-sub{font-size:11px;color:var(--ink4);margin-top:2px}

/* ── AI bar ── */
.ai-bar{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:14px 18px;display:flex;align-items:center;gap:14px;margin-top:2px;flex-wrap:wrap}
.ai-icon{font-size:18px;flex-shrink:0}
.ai-text{flex:1;min-width:180px}
.ai-title{font-size:12.5px;font-weight:500;color:var(--ink)}
.ai-sub{font-size:11.5px;color:var(--ink4);margin-top:1px}
.ai-input{flex:2;min-width:220px;display:flex;gap:8px}
.ai-input input{flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:7px;font-size:12.5px;font-family:inherit;background:var(--surface2);color:var(--ink);outline:none;transition:border-color .15s}
.ai-input input:focus{border-color:var(--blue);background:var(--surface)}
.ai-btns{display:flex;gap:8px;flex-wrap:wrap}

/* ── Tab row ── */
.tab-row{display:flex;gap:2px;padding:4px;background:var(--surface2);border-radius:8px;margin:12px 16px 0}
.tab{flex:1;padding:5px 8px;border-radius:6px;text-align:center;font-size:12px;font-weight:500;color:var(--ink3);cursor:pointer;border:none;background:transparent;font-family:inherit;transition:all .15s}
.tab.active{background:var(--surface);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.08)}

/* ── Empty / loading states ── */
.empty{padding:28px;text-align:center;color:var(--ink4);font-size:12.5px}
.loading{padding:20px;text-align:center;color:var(--ink4);font-size:12px;animation:shimmer 1.5s infinite}
@keyframes shimmer{0%,100%{opacity:.4}50%{opacity:1}}

/* ── Toast ── */
.toast{position:fixed;bottom:24px;right:24px;background:var(--ink);color:#fff;padding:10px 18px;border-radius:9px;font-size:12.5px;z-index:999;transform:translateY(80px);opacity:0;transition:all .3s;pointer-events:none}
.toast.show{transform:translateY(0);opacity:1}

@media(max-width:900px){
  .shell{grid-template-columns:1fr}
  .nav{display:none}
  .kpi-row{grid-template-columns:repeat(2,1fr)}
  .grid3,.grid2{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="shell">

<!-- ── Sidebar ── -->
<nav class="nav">
  <div class="nav-logo">
    <div class="nav-brand">Rooh Gastronomy</div>
    <div class="nav-sub">Inventory Operations</div>
  </div>
  <div class="nav-section">Overview</div>
  <a class="nav-item active" href="#top">
    <span class="nav-dot dot-blue"></span>Dashboard
  </a>
  <a class="nav-item" href="#section-urgent">
    <span class="nav-dot dot-red"></span>Critical Items
    <span class="nav-badge" id="nav-critical">—</span>
  </a>
  <a class="nav-item" href="#section-reorder">
    <span class="nav-dot dot-amber"></span>Reorder Queue
    <span class="nav-badge" id="nav-reorder">—</span>
  </a>
  <div class="nav-section">Departments</div>
  <a class="nav-item" href="#" onclick="setDeptFilter('kitchen');return false">
    <span class="nav-dot dot-green"></span>Kitchen
  </a>
  <a class="nav-item" href="#" onclick="setDeptFilter('bar');return false">
    <span class="nav-dot dot-blue"></span>Bar
  </a>
  <a class="nav-item" href="#" onclick="setDeptFilter('extras');return false">
    <span class="nav-dot dot-amber"></span>Extras
  </a>
  <div class="nav-section">Actions</div>
  <a class="nav-item" href="/docs" target="_blank">
    <span class="nav-dot dot-blue"></span>API Docs
  </a>
  <a class="nav-item" href="#" onclick="triggerBillUpload();return false">
    <span class="nav-dot dot-green"></span>Scan Bill
  </a>
  <div class="nav-footer">
    <div class="nav-user">
      <div class="avatar">RG</div>
      <div>
        <div class="nav-user-name">Rooh Admin</div>
        <div class="nav-user-role" id="nav-sync">Loading...</div>
      </div>
    </div>
  </div>
</nav>

<!-- ── Main content ── -->
<main class="main" id="top">

  <!-- Topbar -->
  <div class="topbar">
    <div>
      <div class="page-title">Stock Dashboard</div>
      <div class="page-sub">Hersbruck, Germany &middot; <span class="sync-dot"></span> <span id="last-sync">connecting...</span></div>
    </div>
    <div class="topbar-right">
      <div class="time-badge" id="clock">--:--</div>
      <button class="btn btn-ghost" onclick="exportCSV()">&#8593; Export CSV</button>
      <button class="btn btn-primary" onclick="openAddItem()">+ Add Item</button>
    </div>
  </div>

  <!-- KPI row -->
  <div class="kpi-row">
    <div class="kpi" onclick="document.getElementById('section-urgent').scrollIntoView({behavior:'smooth'})">
      <div class="kpi-accent" style="background:var(--red)"></div>
      <div class="kpi-label">Critical / Urgent</div>
      <div class="kpi-val red" id="kpi-critical">—</div>
      <div class="kpi-pill pill-red">Immediate action</div>
    </div>
    <div class="kpi" onclick="document.getElementById('section-reorder').scrollIntoView({behavior:'smooth'})">
      <div class="kpi-accent" style="background:#e6a800"></div>
      <div class="kpi-label">Order Soon</div>
      <div class="kpi-val amber" id="kpi-reorder">—</div>
      <div class="kpi-pill pill-amber">Below reorder level</div>
    </div>
    <div class="kpi" onclick="document.getElementById('section-expiry').scrollIntoView({behavior:'smooth'})">
      <div class="kpi-accent" style="background:var(--blue)"></div>
      <div class="kpi-label">Expiring &le; 7 days</div>
      <div class="kpi-val blue" id="kpi-expiring">—</div>
      <div class="kpi-pill pill-amber">Check &amp; use first</div>
    </div>
    <div class="kpi">
      <div class="kpi-accent" style="background:#1a9e5c"></div>
      <div class="kpi-label">Fully Stocked</div>
      <div class="kpi-val green" id="kpi-ok">—</div>
      <div class="kpi-pill pill-green">No action needed</div>
    </div>
  </div>

  <!-- Row 1: Critical table + Alerts -->
  <div class="grid3">
    <!-- Items table -->
    <div class="card" id="section-urgent">
      <div class="card-head">
        <div class="card-title">Critical &amp; Reorder Items</div>
        <div class="card-hint" id="table-hint">Loading...</div>
      </div>
      <div class="tab-row">
        <button class="tab active" onclick="setTab(this,'all')">All</button>
        <button class="tab" onclick="setTab(this,'kitchen')">Kitchen</button>
        <button class="tab" onclick="setTab(this,'bar')">Bar</button>
        <button class="tab" onclick="setTab(this,'extras')">Extras</button>
      </div>
      <div class="tbl-wrap">
        <table class="data">
          <thead>
            <tr>
              <th>Item</th>
              <th>Dept</th>
              <th>Stock</th>
              <th>Reorder</th>
              <th>Fill %</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="items-tbody">
            <tr><td colspan="6" class="loading">Loading inventory...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Alerts panel -->
    <div class="grid-right">
      <div class="card">
        <div class="card-head">
          <div class="card-title">Live Alerts</div>
          <span class="badge b-critical" id="alert-count-badge">— active</span>
        </div>
        <div class="alert-list" id="alerts-list">
          <div class="loading">Loading alerts...</div>
        </div>
      </div>

      <!-- Recent movements -->
      <div class="card">
        <div class="card-head">
          <div class="card-title">Recent Movements</div>
          <div class="card-hint">IN / OUT log</div>
        </div>
        <div id="movements-list">
          <div class="loading">Loading...</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Row 2: Charts -->
  <div class="grid2" id="section-reorder">
    <!-- Bar chart -->
    <div class="card">
      <div class="card-head">
        <div class="card-title">Stock Level by Category</div>
        <div class="card-hint">% of reorder threshold</div>
      </div>
      <div style="padding:16px 18px 12px" id="bar-chart">
        <div class="loading">Loading chart...</div>
      </div>
    </div>

    <!-- Dept breakdown -->
    <div class="card">
      <div class="card-head">
        <div class="card-title">Department Health</div>
        <div class="card-hint" id="dept-hint">—</div>
      </div>
      <div class="dept-grid" id="dept-grid">
        <div class="loading" style="grid-column:span 3">Loading...</div>
      </div>
      <div style="padding:0 16px 14px">
        <canvas id="donut-canvas" width="240" height="120"></canvas>
      </div>
    </div>
  </div>

  <!-- Row 3: Expiry + Bill scanner + Movements -->
  <div class="grid3" id="section-expiry">
    <div class="card">
      <div class="card-head">
        <div class="card-title">Expiry Tracker</div>
        <div class="card-hint">Sorted by urgency</div>
      </div>
      <div id="expiry-list">
        <div class="loading">Loading...</div>
      </div>
    </div>

    <div class="grid-right">
      <!-- Bill scanner -->
      <div class="card">
        <div class="card-head">
          <div class="card-title">Bill Scanner</div>
          <div class="card-hint">GPT-4o Vision</div>
        </div>
        <div class="scan-zone" onclick="triggerBillUpload()" id="scan-zone">
          <input type="file" id="bill-file" accept="image/*,.pdf" onchange="handleBillUpload(this)">
          <div class="scan-icon">&#128247;</div>
          <div class="scan-title">Drop supplier invoice here</div>
          <div class="scan-sub">JPG / PNG &middot; AI extracts items automatically</div>
        </div>
        <div style="padding:0 16px 14px" id="scan-history">
          <div style="font-size:11px;color:var(--ink4);margin-bottom:6px">Recent scans</div>
          <div id="scan-list" class="loading">Loading...</div>
        </div>
      </div>

      <!-- Quick use form -->
      <div class="card">
        <div class="card-head">
          <div class="card-title">Log Usage</div>
          <div class="card-hint">Same as Telegram /use</div>
        </div>
        <div style="padding:12px 16px;display:flex;flex-direction:column;gap:8px">
          <input id="use-item" placeholder="Item name (e.g. Chicken)" style="padding:8px 12px;border:1px solid var(--border);border-radius:7px;font-size:12.5px;font-family:inherit;background:var(--surface2);color:var(--ink);outline:none" onfocus="this.style.borderColor='var(--blue)'" onblur="this.style.borderColor='var(--border)'">
          <div style="display:flex;gap:8px">
            <input id="use-qty" type="number" placeholder="Qty" min="0" step="0.1" style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:7px;font-size:12.5px;font-family:inherit;background:var(--surface2);color:var(--ink);outline:none" onfocus="this.style.borderColor='var(--blue)'" onblur="this.style.borderColor='var(--border)'">
            <select id="use-unit" style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:7px;font-size:12.5px;font-family:inherit;background:var(--surface2);color:var(--ink);outline:none">
              <option>Kg</option><option>Ltr</option><option>Gms</option><option>Ml</option>
              <option>Pieces</option><option>Pack</option><option>Bottle</option>
            </select>
          </div>
          <input id="use-remarks" placeholder="Remarks (optional)" style="padding:8px 12px;border:1px solid var(--border);border-radius:7px;font-size:12.5px;font-family:inherit;background:var(--surface2);color:var(--ink);outline:none" onfocus="this.style.borderColor='var(--blue)'" onblur="this.style.borderColor='var(--border)'">
          <button class="btn btn-primary" style="justify-content:center" onclick="submitUsage()">Log Usage</button>
        </div>
      </div>
    </div>
  </div>

  <!-- AI Query bar -->
  <div class="ai-bar">
    <div class="ai-icon">&#10022;</div>
    <div class="ai-text">
      <div class="ai-title">Ask AI about your inventory</div>
      <div class="ai-sub">Powered by GPT-4o-mini &middot; natural language</div>
    </div>
    <div class="ai-input">
      <input id="ai-q" placeholder="e.g. What should I order today?" onkeydown="if(event.key==='Enter')askAI()">
      <button class="btn btn-primary" onclick="askAI()">Ask &#8594;</button>
    </div>
    <div class="ai-btns">
      <button class="btn btn-ghost" onclick="quickAsk('What is critically low right now?')">Critical now</button>
      <button class="btn btn-ghost" onclick="quickAsk('What is expiring in the next 7 days?')">Expiring soon</button>
      <button class="btn btn-ghost" onclick="quickAsk('Give me a full daily inventory briefing')">Daily briefing</button>
    </div>
  </div>

  <!-- AI Answer box -->
  <div id="ai-answer-box" style="display:none;background:var(--surface);border:1px solid var(--blue-bd);border-radius:var(--radius-lg);padding:16px 20px;margin-top:12px">
    <div style="font-size:11px;color:var(--blue);font-weight:600;margin-bottom:6px;letter-spacing:.3px">AI RESPONSE</div>
    <div id="ai-answer" style="font-size:13px;color:var(--ink2);line-height:1.7;white-space:pre-wrap"></div>
  </div>

</main>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
const API = '';  // Same origin — FastAPI serves this page

let allItems = [];
let allAlerts = [];
let activeTab = 'all';

// ── Utilities ─────────────────────────────────────────────────────────────────

function toast(msg, type='ok'){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = type==='err' ? 'var(--red)' : type==='warn' ? '#c47c00' : 'var(--ink)';
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 3200);
}

function fmtDate(s){
  if(!s) return '—';
  return s;
}

function daysBetween(dateStr){
  if(!dateStr) return null;
  try{
    const parts = dateStr.split('.');
    if(parts.length===3){
      const d = new Date(`${parts[2]}-${parts[1]}-${parts[0]}`);
      const diff = Math.round((d - new Date()) / 86400000);
      return diff;
    }
  }catch(e){}
  return null;
}

// ── Clock ─────────────────────────────────────────────────────────────────────

function updateClock(){
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
setInterval(updateClock, 1000);
updateClock();

// ── Fetch all data ────────────────────────────────────────────────────────────

async function loadAll(){
  try{
    const [dashRes, alertRes, mvRes, billRes] = await Promise.all([
      fetch(API+'/dashboard'),
      fetch(API+'/alerts'),
      fetch(API+'/movements?limit=20'),
      fetch(API+'/bills')
    ]);
    const dash  = await dashRes.json();
    const alerts= await alertRes.json();
    const mvs   = await mvRes.json();
    const bills = await billRes.json();

    allItems  = [...(dash.urgent||[]), ...(dash.order_soon||[]), ...(dash.ok||[])];
    allAlerts = alerts;

    renderKPIs(dash);
    renderTable();
    renderAlerts(alerts);
    renderMovements(mvs);
    renderBarChart(allItems);
    renderDeptGrid(dash);
    renderDonut(dash.summary);
    renderExpiry(allItems);
    renderBillHistory(bills);

    document.getElementById('last-sync').textContent = 'Live · ' + new Date().toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
    document.getElementById('nav-sync').textContent = 'Synced';
  }catch(e){
    console.error(e);
    document.getElementById('last-sync').textContent = 'Connection error';
    toast('Could not reach API. Is the server running?', 'err');
  }
}

// ── KPIs ─────────────────────────────────────────────────────────────────────

function renderKPIs(dash){
  const s = dash.summary || {};
  document.getElementById('kpi-critical').textContent = s.urgent_count ?? '0';
  document.getElementById('kpi-reorder').textContent  = s.order_soon_count ?? '0';
  document.getElementById('kpi-ok').textContent       = s.ok_count ?? '0';
  document.getElementById('nav-critical').textContent = s.urgent_count ?? '0';
  document.getElementById('nav-reorder').textContent  = s.order_soon_count ?? '0';

  // Count expiring ≤7 days
  const expiring = allItems.filter(i=>{
    const d = daysBetween(i.expiry_date);
    return d!==null && d>=0 && d<=7;
  }).length;
  document.getElementById('kpi-expiring').textContent = expiring;
}

// ── Items table ───────────────────────────────────────────────────────────────

function setTab(el, tab){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  activeTab = tab;
  renderTable();
}

function setDeptFilter(dept){
  activeTab = dept;
  document.querySelectorAll('.tab').forEach(t=>{
    t.classList.toggle('active', t.textContent.toLowerCase()===dept || (dept==='all'&&t.textContent==='All'));
  });
  renderTable();
  document.getElementById('section-urgent').scrollIntoView({behavior:'smooth'});
}

function renderTable(){
  const filtered = activeTab==='all'
    ? allItems.filter(i=>i.alert!=='✅ OK')
    : allItems.filter(i=>i.alert!=='✅ OK' && i.department===activeTab);

  const hint = filtered.length + ' items need attention';
  document.getElementById('table-hint').textContent = hint;

  if(!filtered.length){
    document.getElementById('items-tbody').innerHTML =
      '<tr><td colspan="6" class="empty">All items at healthy stock levels ✓</td></tr>';
    return;
  }

  document.getElementById('items-tbody').innerHTML = filtered.map(item=>{
    const reorder = item.reorder_level || 1;
    const pct = Math.min(100, Math.round((item.current_stock / reorder)*100));
    const pfClass = item.current_stock <= (item.critical_level||0) ? 'pf-red'
                  : pct < 80 ? 'pf-amber' : 'pf-green';
    const svClass = item.current_stock <= (item.critical_level||0) ? 'sv-red'
                  : pct < 80 ? 'sv-amber' : 'sv-ok';
    const badge = item.alert === '✅ OK' ? ''
      : item.alert?.includes('URGENT') ? '<span class="badge b-critical">Critical</span>'
      : item.alert?.includes('ORDER')  ? '<span class="badge b-reorder">Reorder</span>'
      : '<span class="badge b-expiring">Expiring</span>';

    return `<tr>
      <td>
        <div class="item-name">${item.name}</div>
        <div class="item-sub">${item.storage||''}</div>
      </td>
      <td><span class="badge b-dept">${(item.department||'').toUpperCase()}</span></td>
      <td><span class="sv ${svClass}">${item.current_stock} ${item.unit}</span></td>
      <td><span class="sv" style="color:var(--ink4)">${item.reorder_level||'—'}</span></td>
      <td>
        <div class="prog-wrap">
          <div class="prog-bg"><div class="prog-fill ${pfClass}" style="width:${pct}%"></div></div>
        </div>
      </td>
      <td>${badge}</td>
    </tr>`;
  }).join('');
}

// ── Alerts ────────────────────────────────────────────────────────────────────

function renderAlerts(alerts){
  const badge = document.getElementById('alert-count-badge');
  badge.textContent = (alerts.length||0) + ' active';
  badge.className = 'badge ' + (alerts.length>0 ? 'b-critical' : 'b-ok');

  if(!alerts.length){
    document.getElementById('alerts-list').innerHTML = '<div class="empty">No active alerts ✓</div>';
    return;
  }

  document.getElementById('alerts-list').innerHTML = alerts.slice(0,8).map(a=>{
    const icon = a.type==='critical'?'⛔':a.type==='expired'?'⛔':'⚠️';
    const iconClass = a.type==='critical'||a.type==='expired' ? 'ai-red' : 'ai-amber';
    const time = new Date(a.since).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
    return `<div class="alert-row">
      <div class="al-icon ${iconClass}">${icon}</div>
      <div class="al-body">
        <div class="al-text">${a.message}</div>
        <div class="al-meta">${time}</div>
      </div>
      <button class="al-resolve" onclick="resolveAlert(${a.id}, this)">Resolve</button>
    </div>`;
  }).join('');
}

async function resolveAlert(id, btn){
  btn.disabled = true;
  btn.textContent = '...';
  try{
    await fetch(API+`/alerts/${id}/resolve`, {method:'POST'});
    await loadAll();
    toast('Alert resolved');
  }catch(e){ toast('Error resolving alert','err'); btn.disabled=false; btn.textContent='Resolve'; }
}

// ── Movements ─────────────────────────────────────────────────────────────────

function renderMovements(mvs){
  if(!mvs.length){
    document.getElementById('movements-list').innerHTML = '<div class="empty">No movements yet</div>';
    return;
  }
  document.getElementById('movements-list').innerHTML = mvs.slice(0,8).map(m=>{
    const dirClass = m.direction==='IN' ? 'mv-in' : 'mv-out';
    const t = new Date(m.at).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
    return `<div class="mv-row">
      <div class="mv-dir ${dirClass}">${m.direction}</div>
      <div class="mv-item">${m.item}</div>
      <div class="mv-qty">${m.quantity} ${m.unit}</div>
      <div class="mv-by">${m.by||'—'}</div>
      <div class="mv-src">${m.source||''}</div>
      <div style="font-size:10.5px;color:var(--ink4);margin-left:auto">${t}</div>
    </div>`;
  }).join('');
}

// ── Bar chart ─────────────────────────────────────────────────────────────────

function renderBarChart(items){
  const catMap = {};
  items.forEach(i=>{
    const cat = i.category||'Other';
    if(!catMap[cat]) catMap[cat]={stock:0,reorder:0,count:0};
    catMap[cat].stock   += i.current_stock||0;
    catMap[cat].reorder += i.reorder_level||0;
    catMap[cat].count++;
  });

  const rows = Object.entries(catMap)
    .filter(([,v])=>v.reorder>0)
    .map(([cat,v])=>({cat, pct:Math.min(100,Math.round((v.stock/v.reorder)*100))}))
    .sort((a,b)=>a.pct-b.pct)
    .slice(0,9);

  document.getElementById('bar-chart').innerHTML = rows.map(r=>{
    const col = r.pct<=25 ? 'var(--red)' : r.pct<=70 ? '#e6a800' : '#1a9e5c';
    return `<div class="bar-row">
      <div class="bar-label">${r.cat}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${r.pct}%;background:${col}"></div></div>
      <div class="bar-val">${r.pct}%</div>
    </div>`;
  }).join('');
}

// ── Dept grid ─────────────────────────────────────────────────────────────────

function renderDeptGrid(dash){
  const depts = ['kitchen','bar','extras'];
  const all = [...(dash.urgent||[]),...(dash.order_soon||[]),...(dash.ok||[])];
  document.getElementById('dept-hint').textContent = all.length + ' items total';

  document.getElementById('dept-grid').innerHTML = depts.map(d=>{
    const total   = all.filter(i=>i.department===d).length;
    const critical= all.filter(i=>i.department===d && i.alert?.includes('URGENT')).length;
    const col = critical>0 ? 'var(--red)' : total>0 ? 'var(--green)' : 'var(--ink4)';
    return `<div class="dept-card">
      <div class="dept-num" style="color:${col}">${total}</div>
      <div class="dept-name">${d.charAt(0).toUpperCase()+d.slice(1)}</div>
      <div class="dept-crit" style="color:${critical>0?'var(--red)':'var(--ink4)'}">
        ${critical>0?critical+' critical':'OK'}
      </div>
    </div>`;
  }).join('');
}

// ── Donut ─────────────────────────────────────────────────────────────────────

function renderDonut(s){
  if(!s) return;
  const canvas = document.getElementById('donut-canvas');
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0,0,240,120);
  const total = (s.urgent_count||0)+(s.order_soon_count||0)+(s.ok_count||0);
  if(!total) return;
  const segs = [
    {count:s.urgent_count||0,   color:'#d63b3b', label:'Critical'},
    {count:s.order_soon_count||0,color:'#e6a800',label:'Order Soon'},
    {count:s.ok_count||0,       color:'#1a9e5c', label:'OK'},
  ];
  let angle = -Math.PI/2;
  const cx=60,cy=60,r=48,lw=16;
  segs.forEach(seg=>{
    if(!seg.count) return;
    const a=(seg.count/total)*Math.PI*2;
    ctx.beginPath();
    ctx.arc(cx,cy,r,angle,angle+a);
    ctx.strokeStyle=seg.color;
    ctx.lineWidth=lw;
    ctx.stroke();
    angle+=a;
  });
  ctx.font='600 16px DM Sans,sans-serif';
  ctx.fillStyle='#0f1117';
  ctx.textAlign='center';
  ctx.textBaseline='middle';
  ctx.fillText(total,cx,cy-5);
  ctx.font='400 10px DM Sans,sans-serif';
  ctx.fillStyle='#6b6f7e';
  ctx.fillText('items',cx,cy+9);

  // Legend
  let lx=130,ly=14;
  segs.forEach(seg=>{
    if(!seg.count) return;
    ctx.fillStyle=seg.color;
    ctx.fillRect(lx,ly,10,10);
    ctx.fillStyle='#3a3d4a';
    ctx.font='400 11px DM Sans,sans-serif';
    ctx.textAlign='left';
    ctx.textBaseline='top';
    ctx.fillText(seg.label+' ('+seg.count+')',lx+14,ly);
    ly+=20;
  });
}

// ── Expiry ────────────────────────────────────────────────────────────────────

function renderExpiry(items){
  const withExpiry = items
    .filter(i=>i.expiry_date)
    .map(i=>({...i, days:daysBetween(i.expiry_date)}))
    .filter(i=>i.days!==null)
    .sort((a,b)=>a.days-b.days)
    .slice(0,12);

  if(!withExpiry.length){
    document.getElementById('expiry-list').innerHTML='<div class="empty">No expiry data available</div>';
    return;
  }

  document.getElementById('expiry-list').innerHTML = withExpiry.map(i=>{
    const cls = i.days<0 ? 'ed-expired' : i.days<=7 ? 'ed-soon' : 'ed-ok';
    const label = i.days<0 ? 'Expired' : i.days===0 ? 'Today' : i.days+'d';
    return `<div class="exp-row">
      <div>
        <div class="item-name">${i.name}</div>
        <div class="item-sub">${i.category||''} · ${i.storage||''}</div>
      </div>
      <div style="text-align:right">
        <div class="exp-days ${cls}">${label}</div>
        <div style="font-size:10px;color:var(--ink4);margin-top:2px">${fmtDate(i.expiry_date)}</div>
      </div>
    </div>`;
  }).join('');
}

// ── Bill scanner ──────────────────────────────────────────────────────────────

function triggerBillUpload(){
  document.getElementById('bill-file').click();
}

async function handleBillUpload(input){
  const file = input.files[0];
  if(!file) return;
  const zone = document.getElementById('scan-zone');
  zone.querySelector('.scan-title').textContent = 'Scanning...';
  zone.querySelector('.scan-sub').textContent = 'AI is reading the invoice';
  toast('Uploading bill — AI scanning...');

  const fd = new FormData();
  fd.append('file', file);
  fd.append('uploaded_by', 'manager');
  fd.append('auto_apply', 'false');

  try{
    const res  = await fetch(API+'/bills/scan', {method:'POST', body:fd});
    const data = await res.json();
    if(data.error){ toast('Scan error: '+data.error,'err'); }
    else{
      toast(`Found ${data.items_found} items in bill. Review then apply.`,'ok');
      const apply = confirm(`Bill scanned!\nSupplier: ${data.supplier||'Unknown'}\nItems found: ${data.items_found}\n\nApply to stock now?`);
      if(apply){
        await fetch(API+`/bills/${data.scan_id}/apply?applied_by=manager`,{method:'POST'});
        toast('Stock updated from bill!');
        await loadAll();
      }
    }
  }catch(e){ toast('Bill scan failed: '+e.message,'err'); }

  zone.querySelector('.scan-title').textContent = 'Drop supplier invoice here';
  zone.querySelector('.scan-sub').textContent = 'JPG / PNG · AI extracts items automatically';
  input.value='';
  loadBillHistory();
}

async function loadBillHistory(){
  try{
    const res  = await fetch(API+'/bills');
    const bills= await res.json();
    renderBillHistory(bills);
  }catch(e){}
}

function renderBillHistory(bills){
  const el = document.getElementById('scan-list');
  if(!bills.length){ el.innerHTML='<div style="color:var(--ink4);font-size:12px">No scans yet</div>'; return; }
  el.innerHTML = bills.slice(0,4).map(b=>{
    const t = new Date(b.created_at).toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit'});
    return `<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--surface2);font-size:12px">
      <span style="color:var(--ink2)">${b.uploaded_by||'manager'} · ${b.items_count} items</span>
      <span style="display:flex;gap:6px;align-items:center">
        <span style="font-size:10.5px;color:var(--ink4)">${t}</span>
        <span class="badge ${b.applied?'b-ok':'b-reorder'}" style="font-size:10px">${b.applied?'Applied':'Pending'}</span>
      </span>
    </div>`;
  }).join('');
}

// ── Quick use form ────────────────────────────────────────────────────────────

async function submitUsage(){
  const item    = document.getElementById('use-item').value.trim();
  const qty     = parseFloat(document.getElementById('use-qty').value);
  const unit    = document.getElementById('use-unit').value;
  const remarks = document.getElementById('use-remarks').value.trim();

  if(!item || isNaN(qty) || qty<=0){ toast('Enter item name and quantity','warn'); return; }

  try{
    const res = await fetch(API+'/stock/use', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({item_name:item, quantity:qty, unit, entered_by:'dashboard', remarks:remarks||null})
    });
    const data = await res.json();
    if(res.ok){
      toast(`Used ${qty} ${unit} of ${item}. Remaining: ${data.remaining}`);
      document.getElementById('use-item').value='';
      document.getElementById('use-qty').value='';
      document.getElementById('use-remarks').value='';
      await loadAll();
    } else {
      toast(data.detail||'Error logging usage','err');
    }
  }catch(e){ toast('Error: '+e.message,'err'); }
}

// ── AI Query ──────────────────────────────────────────────────────────────────

async function askAI(){
  const q = document.getElementById('ai-q').value.trim();
  if(!q){ toast('Enter a question','warn'); return; }
  quickAsk(q);
}

async function quickAsk(q){
  document.getElementById('ai-q').value = q;
  const box = document.getElementById('ai-answer-box');
  const ans = document.getElementById('ai-answer');
  box.style.display='block';
  ans.textContent = 'Thinking...';
  try{
    const res  = await fetch(API+'/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
    const data = await res.json();
    ans.textContent = data.answer||'No response';
  }catch(e){ ans.textContent = 'Error: '+e.message; }
}

// ── Export ────────────────────────────────────────────────────────────────────

function exportCSV(){
  if(!allItems.length){ toast('No data to export','warn'); return; }
  const header = 'Name,Department,Category,Stock,Unit,Reorder Level,Critical Level,Expiry,Status\\n';
  const rows = allItems.map(i=>
    `"${i.name}","${i.department}","${i.category}",${i.current_stock},"${i.unit}",${i.reorder_level||''},${i.critical_level||''},"${i.expiry_date||''}","${i.alert||''}"`
  ).join('\\n');
  const blob = new Blob([header+rows],{type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'rooh_inventory_'+new Date().toISOString().slice(0,10)+'.csv';
  a.click();
  toast('CSV exported');
}

// ── Add item modal (simple prompt-based) ─────────────────────────────────────

function openAddItem(){
  const name = prompt('Item name:');
  if(!name) return;
  const category = prompt('Category (e.g. Meat, Vegetable, Spirit):') || 'General';
  const department = prompt('Department (kitchen / bar / extras):') || 'kitchen';
  const unit = prompt('Unit (Kg, Ltr, Pieces, etc.):') || 'Kg';
  const stock = parseFloat(prompt('Current stock quantity:') || '0');
  const reorder = parseFloat(prompt('Reorder level:') || '0');
  const critical = parseFloat(prompt('Critical level:') || '0');

  fetch(API+'/items',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name,category,department,unit,current_stock:stock,reorder_level:reorder,critical_level:critical})
  }).then(r=>r.json()).then(d=>{
    if(d.message){ toast('Item added: '+name); loadAll(); }
    else toast(d.detail||'Error adding item','err');
  }).catch(e=>toast('Error: '+e.message,'err'));
}

// ── Auto-refresh every 60s ────────────────────────────────────────────────────

loadAll();
setInterval(loadAll, 60000);
</script>
</body>
</html>"""
    return HTMLResponse(content=html)

# ── Alerts ────────────────────────────────────────────────────────────────────

@app.get("/alerts", summary="All active alerts")
def get_alerts(db: Session = Depends(get_db)):
    _check_alerts()
    alerts = db.query(Alert).filter(Alert.resolved == 0).order_by(Alert.created_at.desc()).all()
    return [{"id": a.id, "item": a.item_name, "type": a.alert_type,
             "message": a.message, "since": a.created_at.isoformat()} for a in alerts]

@app.post("/alerts/{alert_id}/resolve", summary="Resolve an alert")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    a = db.query(Alert).filter(Alert.id == alert_id).first()
    if not a:
        raise HTTPException(404, "Alert not found")
    a.resolved = 1
    db.commit()
    return {"message": "Resolved"}

# ── AI Query ──────────────────────────────────────────────────────────────────

@app.post("/query", summary="Ask AI about inventory in plain English")
def nl_query(query: NLQuery, db: Session = Depends(get_db)):
    items = db.query(InventoryItem).all()
    snapshot = json.dumps([
        {"name": i.name, "department": i.department, "category": i.category,
         "current_stock": i.current_stock, "unit": i.unit,
         "reorder_level": i.reorder_level, "critical_level": i.critical_level,
         "expiry_date": i.expiry_date, "storage": i.storage}
        for i in items
    ], indent=2)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"""You are the AI inventory assistant for Rooh Gastronomy, 
an Indian restaurant in Hersbruck, Germany. Answer questions about inventory concisely.
Current date: {date.today().strftime('%d.%m.%Y')}

Inventory:\n{snapshot}"""},
            {"role": "user", "content": query.question}
        ],
        max_tokens=400
    )
    answer = response.choices[0].message.content
    db.add(QueryLog(user_query=query.question, ai_response=answer))
    db.commit()
    return {"question": query.question, "answer": answer}

@app.get("/movements", summary="Stock movement log")
def movements(limit: int = 50, db: Session = Depends(get_db)):
    mvs = db.query(StockMovement).order_by(StockMovement.created_at.desc()).limit(limit).all()
    return [{"id": m.id, "item": m.item_name, "direction": m.direction,
             "quantity": m.quantity, "unit": m.unit, "source": m.source,
             "by": m.entered_by, "remarks": m.remarks,
             "at": m.created_at.isoformat()} for m in mvs]

@app.get("/health")
def health():
    return {"status": "ok", "service": "Rooh Inventory v2"}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_item(db, name: str):
    """Case-insensitive partial match for item lookup."""
    name_lower = name.lower().strip()
    items = db.query(InventoryItem).all()
    # Exact match first
    for i in items:
        if i.name.lower() == name_lower:
            return i
    # Partial match
    for i in items:
        if name_lower in i.name.lower() or i.name.lower() in name_lower:
            return i
    return None

def _item_dict(i: InventoryItem) -> dict:
    return {
        "id": i.id, "name": i.name, "category": i.category,
        "sub_category": i.sub_category, "department": i.department,
        "storage": i.storage, "unit": i.unit,
        "current_stock": i.current_stock,
        "reorder_level": i.reorder_level,
        "critical_level": i.critical_level,
        "expiry_date": i.expiry_date, "supplier": i.supplier,
        "quality": i.quality,
        "last_updated": i.last_updated.isoformat() if i.last_updated else None,
        "updated_by": i.updated_by,
    }
