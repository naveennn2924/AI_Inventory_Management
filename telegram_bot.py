"""
Rooh Gastronomy – Telegram Inventory Bot
-----------------------------------------
Staff use this bot to log what they took from inventory.
The bot calls the FastAPI backend to update stock.

Setup:
  1. Create a bot via @BotFather on Telegram → get token
  2. Set TELEGRAM_BOT_TOKEN in .env
  3. Run: python telegram_bot.py

Commands:
  /use chicken 2 kg          → deducts 2 kg chicken
  /use onion 3 kg tomato 500 gms  → multiple items at once
  /use butter 1 pack remarks: for biryani
  /stock                     → shows low/critical items
  /check chicken             → check specific item stock
  /alerts                    → shows active alerts
  /help                      → command list
"""

import os, re, json, asyncio, httpx
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
API_BASE   = os.getenv("INVENTORY_API_URL", "http://localhost:8001")

# User roles from Excel: rohan=BAR, kitchen=KITCHEN, procure=PROCUREMENT, admin=ADMIN
ALLOWED_USERS = {}  # filled from /users endpoint or hardcoded
USER_PINS = {
    "rohan": {"pin": "1111", "role": "BAR"},
    "kitchen": {"pin": "2222", "role": "KITCHEN"},
    "procure": {"pin": "4444", "role": "PROCUREMENT"},
    "admin": {"pin": "9999", "role": "ADMIN"},
}
# session: {telegram_user_id: username}
sessions = {}

# ── Auth ──────────────────────────────────────────────────────────────────────

async def require_auth(update: Update) -> str | None:
    uid = update.effective_user.id
    if uid in sessions:
        return sessions[uid]
    await update.message.reply_text(
        "🔐 Please login first.\nSend: /login <username> <pin>\n\nExample: /login kitchen 2222"
    )
    return None

async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /login <username> <pin>\nExample: /login kitchen 2222")
        return
    username, pin = args[0].lower(), args[1]
    user_data = USER_PINS.get(username)
    if user_data and user_data["pin"] == pin:
        sessions[update.effective_user.id] = username
        await update.message.reply_text(
            f"✅ Logged in as *{username.upper()}* ({user_data['role']})\n\n"
            "Available commands:\n"
            "/use <item> <qty> <unit> — Log usage\n"
            "/stock — View low/critical stock\n"
            "/check <item> — Check item stock\n"
            "/alerts — Active alerts\n"
            "/help — All commands",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Invalid username or PIN. Try again.")

async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sessions.pop(update.effective_user.id, None)
    await update.message.reply_text("👋 Logged out.")

# ── /use command ──────────────────────────────────────────────────────────────

async def cmd_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Parse usage from message. Supports multiple items:
    /use chicken 2 kg
    /use onion 3 kg tomato 500 gms butter 1 pack
    /use lamb 1.5 kg remarks: for biryani special
    """
    username = await require_auth(update)
    if not username:
        return

    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Usage: /use <item> <qty> <unit> [remarks: ...]\n"
            "Example: /use chicken 2 kg\n"
            "Multiple: /use onion 3 kg tomato 500 gms"
        )
        return

    # Extract optional remarks
    remarks = None
    if "remarks:" in text.lower():
        parts = re.split(r"remarks:", text, flags=re.IGNORECASE)
        text = parts[0].strip()
        remarks = parts[1].strip() if len(parts) > 1 else None

    # Parse: "chicken 2 kg onion 3 kg ..."
    # Pattern: word(s) followed by number followed by unit
    pattern = r"([a-zA-Z][a-zA-Z\s]+?)\s+(\d+\.?\d*)\s+(kg|gms|gm|ltr|l|ml|pieces|pcs|pack|packs|bottle|bottles|bunch|units?)\b"
    matches = re.findall(pattern, text, re.IGNORECASE)

    if not matches:
        await update.message.reply_text(
            "❓ Could not parse. Format: /use <item> <qty> <unit>\n"
            "Example: /use chicken 2 kg"
        )
        return

    items_payload = []
    for item_name, qty, unit in matches:
        # Normalise unit
        unit = unit.strip().lower()
        unit_map = {"gm": "Gms", "gms": "Gms", "kg": "Kg", "l": "Ltr", "ltr": "Ltr",
                    "ml": "Ml", "pcs": "Pieces", "pieces": "Pieces", "pack": "Pack",
                    "packs": "Pack", "bottle": "Bottle", "bottles": "Bottle",
                    "bunch": "Bunch", "unit": "Unit", "units": "Unit"}
        unit_norm = unit_map.get(unit, unit.capitalize())
        items_payload.append({
            "item_name": item_name.strip(),
            "quantity": float(qty),
            "unit": unit_norm,
            "entered_by": username,
            "remarks": remarks
        })

    if not items_payload:
        await update.message.reply_text("❓ No valid items found.")
        return

    # Call API
    async with httpx.AsyncClient() as http:
        try:
            resp = await http.post(f"{API_BASE}/telegram/usage", json={
                "user": username,
                "items": items_payload
            }, timeout=15)
            data = resp.json()
        except Exception as e:
            await update.message.reply_text(f"⚠️ API error: {e}")
            return

    # Format response
    lines = [f"📦 *Usage logged by {username.upper()}*\n"]
    for r in data.get("results", []):
        item = r.get("item")
        status = r.get("status")
        if status == "ok":
            remaining = r.get("remaining", 0)
            unit = r.get("unit", "")
            emoji = "🟡" if remaining < 3 else "✅"
            lines.append(f"{emoji} *{item}*: used → {remaining:.2f} {unit} remaining")
        elif status == "insufficient":
            avail = r.get("available", 0)
            unit = r.get("unit", "")
            lines.append(f"⚠️ *{item}*: only {avail} {unit} available — not enough!")
        else:
            lines.append(f"❓ *{item}*: not found in inventory")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ── /stock command ────────────────────────────────────────────────────────────

async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = await require_auth(update)
    if not username:
        return

    async with httpx.AsyncClient() as http:
        try:
            resp = await http.get(f"{API_BASE}/dashboard", timeout=15)
            data = resp.json()
        except Exception as e:
            await update.message.reply_text(f"⚠️ API error: {e}")
            return

    urgent = data.get("urgent", [])
    order_soon = data.get("order_soon", [])
    s = data.get("summary", {})

    lines = [f"📊 *Rooh Gastronomy – Stock Status*\n"]
    lines.append(f"Total items: {s.get('total_items',0)} | 🔴 Urgent: {s.get('urgent_count',0)} | 🟡 Order soon: {s.get('order_soon_count',0)}\n")

    if urgent:
        lines.append("🔴 *URGENT / CRITICAL:*")
        for i in urgent[:10]:
            exp = f" (exp: {i.get('expiry_date','')})" if i.get("expiry_date") else ""
            lines.append(f"  • {i['name']}: {i['current_stock']} {i['unit']}{exp}")

    if order_soon:
        lines.append("\n🟡 *ORDER SOON:*")
        for i in order_soon[:10]:
            lines.append(f"  • {i['name']}: {i['current_stock']} {i['unit']} (reorder: {i.get('reorder_level','-')})")

    if not urgent and not order_soon:
        lines.append("✅ All stock levels are fine!")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ── /check command ────────────────────────────────────────────────────────────

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = await require_auth(update)
    if not username:
        return
    if not context.args:
        await update.message.reply_text("Usage: /check <item name>\nExample: /check chicken")
        return
    item_name = " ".join(context.args)
    async with httpx.AsyncClient() as http:
        try:
            resp = await http.get(f"{API_BASE}/items", timeout=10)
            items = resp.json()
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
            return

    found = [i for i in items if item_name.lower() in i["name"].lower()]
    if not found:
        await update.message.reply_text(f"❓ No item matching '{item_name}' found.")
        return

    lines = [f"🔍 *Items matching '{item_name}':*\n"]
    for i in found:
        status_emoji = "🔴" if i.get("current_stock", 0) <= (i.get("critical_level") or 0) else \
                       "🟡" if i.get("current_stock", 0) <= (i.get("reorder_level") or 0) else "✅"
        exp = f"\n  Expiry: {i['expiry_date']}" if i.get("expiry_date") else ""
        lines.append(
            f"{status_emoji} *{i['name']}* ({i['department'].upper()})\n"
            f"  Stock: {i['current_stock']} {i['unit']}\n"
            f"  Reorder: {i.get('reorder_level','—')} | Critical: {i.get('critical_level','—')}"
            f"{exp}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

# ── /alerts command ───────────────────────────────────────────────────────────

async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = await require_auth(update)
    if not username:
        return
    async with httpx.AsyncClient() as http:
        try:
            resp = await http.get(f"{API_BASE}/alerts", timeout=10)
            alerts = resp.json()
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
            return
    if not alerts:
        await update.message.reply_text("✅ No active alerts!")
        return
    lines = [f"🚨 *Active Alerts ({len(alerts)}):*\n"]
    for a in alerts[:15]:
        lines.append(f"• {a['message']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ── /help command ─────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍽️ *Rooh Gastronomy Inventory Bot*\n\n"
        "*/login* `<username> <pin>` — Login (required first)\n"
        "*/use* `<item> <qty> <unit>` — Log what you used\n"
        "  Example: `/use chicken 2 kg`\n"
        "  Multiple: `/use onion 3 kg tomato 500 gms`\n"
        "*/stock* — View critical & low stock\n"
        "*/check* `<item>` — Check a specific item\n"
        "*/alerts* — View active alerts\n"
        "*/logout* — Logout\n\n"
        "📌 Units: kg, gms, ltr, ml, pieces, pack, bottle, bunch",
        parse_mode="Markdown"
    )

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        print("   Get a token from @BotFather on Telegram")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout",cmd_logout))
    app.add_handler(CommandHandler("use",   cmd_use))
    app.add_handler(CommandHandler("stock", cmd_stock))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("alerts",cmd_alerts))
    print("🤖 Rooh Inventory Bot running... (Ctrl+C to stop)")
    app.run_polling()

if __name__ == "__main__":
    main()
