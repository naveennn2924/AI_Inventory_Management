# 🍽️ Rooh Gastronomy – AI Inventory System v2

> Production AI inventory system for Rooh Gastronomy, Hersbruck, Germany  
> Built during AI & Process Automation Internship (Nov 2025 – Mar 2026)

## What's New in v2

| Feature | Description |
|---------|-------------|
| 📸 **Bill Scan** | Upload supplier bill photo → GPT-4o Vision extracts items → auto stock update |
| 📱 **Telegram Bot** | Staff log usage directly in Telegram: `/use chicken 2 kg` |
| 📊 **Live Dashboard** | Browser dashboard with 🔴 Urgent / 🟡 Order Soon / ✅ OK |
| 🏪 **Multi-Department** | Kitchen, Bar, Extras — all in one system |
| 📦 **Real Data** | Seeded from actual Rooh Gastronomy Excel inventory |

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env          # add your OPENAI_API_KEY

# Start the API
uvicorn main:app --reload --port 8001

# Open dashboard in browser
http://localhost:8001/dashboard/ui

# Start Telegram bot (separate terminal, needs TELEGRAM_BOT_TOKEN)
python telegram_bot.py
```

## Dashboard

Open **http://localhost:8001/dashboard/ui** — live visual dashboard:

- 🔴 **URGENT** — critical stock or expired items
- 🟡 **ORDER SOON** — below reorder level or expiring within 7 days
- ✅ **OK** — all good

## Bill Scan (AI Vision)

Upload a photo of a supplier invoice → AI reads item names, quantities, units → stock updated automatically.

```bash
# Upload and preview (don't apply yet)
curl -X POST http://localhost:8001/bills/scan \
  -F "file=@invoice_photo.jpg" \
  -F "uploaded_by=manager" \
  -F "auto_apply=false"

# Upload and immediately apply to stock
curl -X POST http://localhost:8001/bills/scan \
  -F "file=@invoice_photo.jpg" \
  -F "auto_apply=true"

# Apply a saved scan after review
curl -X POST http://localhost:8001/bills/3/apply?applied_by=manager
```

## Telegram Bot

**Setup:**
1. Message @BotFather → `/newbot` → copy token
2. Set `TELEGRAM_BOT_TOKEN=...` in `.env`
3. Run `python telegram_bot.py`

**Staff commands:**
```
/login kitchen 2222         → login with PIN from Users sheet
/use chicken 2 kg           → deduct 2kg chicken from stock
/use onion 3 kg tomato 500 gms  → multiple items at once
/use butter 1 pack remarks: biryani service
/stock                      → see low/critical items
/check lamb                 → check specific item
/alerts                     → active stock alerts
```

**Users (from Excel):**
| Username | PIN | Role |
|----------|-----|------|
| rohan | 1111 | BAR |
| kitchen | 2222 | KITCHEN |
| procure | 4444 | PROCUREMENT |
| admin | 9999 | ADMIN |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard/ui` | **Visual dashboard in browser** |
| GET | `/dashboard` | Dashboard JSON data |
| POST | `/bills/scan` | Upload bill image → AI extracts |
| POST | `/bills/{id}/apply` | Apply scanned bill to stock |
| POST | `/telegram/usage` | Telegram bot usage logging |
| POST | `/stock/use` | Manual usage (OUT) |
| POST | `/stock/add` | Manual stock addition (IN) |
| GET | `/items` | List all items (filter by ?department=bar) |
| POST | `/items` | Add item |
| PATCH | `/items/{id}` | Update item |
| GET | `/alerts` | Active alerts |
| POST | `/query` | Ask AI in plain English |
| GET | `/movements` | Full stock movement log |

## Project Structure

```
├── main.py           # FastAPI app — all endpoints
├── models.py         # SQLAlchemy DB models
├── seed.py           # Real Rooh Gastronomy data from Excel
├── telegram_bot.py   # Telegram bot for staff usage logging
├── requirements.txt
├── .env.example
└── .devcontainer/    # GitHub Codespaces config
```

## Example AI Queries

```bash
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What items in the kitchen are running critically low?"}'

curl -X POST http://localhost:8001/query \
  -d '{"question": "Which bar items need to be reordered before the weekend?"}'

curl -X POST http://localhost:8001/query \
  -d '{"question": "What is expiring in the next 2 weeks?"}'
```
