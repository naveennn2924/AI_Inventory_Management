"""
Seed the database with real Rooh Gastronomy inventory data
from the Excel sheets. Run once on first startup.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from models import SessionLocal, InventoryItem, create_tables

SEED_DATA = [
    # ── KITCHEN – Live Inventory (Inventory_Live sheet) ──────────────────────
    dict(name="H-Schlag Sahne",       category="Dairy",          department="kitchen", storage="Cold Store",     unit="Ltr",    current_stock=19.0,  reorder_level=10.0, critical_level=5.0,  expiry_date="26.04.2026", quality="Good"),
    dict(name="Sunflower Oil",         category="Oil",            department="kitchen", storage="Dry Store",      unit="Ltr",    current_stock=20.0,  reorder_level=40.0, critical_level=20.0, expiry_date=None,         quality="Good"),
    dict(name="Fries Oil",             category="Oil",            department="kitchen", storage="Dry Store",      unit="Ltr",    current_stock=20.0,  reorder_level=40.0, critical_level=20.0, expiry_date=None,         quality="Good"),
    dict(name="Chapathi Flour",        category="Dry Goods",      department="kitchen", storage="Dry Store",      unit="Kg",     current_stock=30.0,  reorder_level=15.0, critical_level=10.0, expiry_date="30.11.2026", quality="Good"),
    dict(name="Maida",                 category="Dry Goods",      department="kitchen", storage="Dry Store",      unit="Kg",     current_stock=40.0,  reorder_level=30.0, critical_level=20.0, expiry_date="30.11.2026", quality="Good"),
    dict(name="Hähnchen Schnitzel",    category="Frozen",         department="kitchen", storage="Freezer",        unit="Kg",     current_stock=10.0,  reorder_level=5.0,  critical_level=5.0,  expiry_date=None,         quality="Good"),
    dict(name="Pangasius Fish Fillets",category="Frozen Seafood", department="kitchen", storage="Freezer",        unit="Kg",     current_stock=3.65,  reorder_level=2.0,  critical_level=1.0,  expiry_date="05.08.2027", quality="Good"),
    dict(name="Broccoli (Frozen)",     category="Frozen Veg",     department="kitchen", storage="Freezer",        unit="Kg",     current_stock=6.75,  reorder_level=3.0,  critical_level=1.0,  expiry_date="01.03.2028", quality="Good"),
    dict(name="Cut Pineapple (Frozen)",category="Frozen Fruit",   department="kitchen", storage="Freezer",        unit="Kg",     current_stock=3.0,   reorder_level=1.0,  critical_level=1.0,  expiry_date="15.12.2026", quality="Good"),
    dict(name="Big Prawns",            category="Frozen Seafood", department="kitchen", storage="Freezer",        unit="Kg",     current_stock=10.0,  reorder_level=5.0,  critical_level=3.0,  expiry_date="10.10.2026", quality="Good"),
    dict(name="Tiger Shrimps",         category="Frozen Seafood", department="kitchen", storage="Freezer",        unit="Kg",     current_stock=4.0,   reorder_level=5.0,  critical_level=3.0,  expiry_date="01.04.2027", quality="Good"),
    dict(name="French Fries",          category="Frozen",         department="kitchen", storage="Freezer",        unit="Kg",     current_stock=5.0,   reorder_level=3.0,  critical_level=2.0,  expiry_date="18.09.2027", quality="Good"),
    dict(name="Nuggets",               category="Frozen",         department="kitchen", storage="Freezer",        unit="Kg",     current_stock=8.0,   reorder_level=5.0,  critical_level=3.0,  expiry_date="09.01.2027", quality="Good"),
    dict(name="Chocolate Samosa",      category="Dessert",        department="kitchen", storage="Freezer",        unit="Pieces", current_stock=40.0,  reorder_level=20.0, critical_level=20.0, expiry_date=None,         quality="Good"),
    dict(name="Chocolate Ice Cream",   category="Dessert",        department="kitchen", storage="Freezer",        unit="Kg",     current_stock=2.25,  reorder_level=1.5,  critical_level=1.0,  expiry_date="01.10.2027", quality="Good"),
    dict(name="Vanilla Ice Cream",     category="Dessert",        department="kitchen", storage="Freezer",        unit="Kg",     current_stock=2.45,  reorder_level=1.5,  critical_level=1.0,  expiry_date="01.10.2027", quality="Good"),
    dict(name="Dahi Kabab",            category="Frozen",         department="kitchen", storage="Freezer",        unit="Pieces", current_stock=45.0,  reorder_level=35.0, critical_level=25.0, expiry_date=None,         quality="Good"),
    dict(name="Butter",                category="Dairy",          department="kitchen", storage="Chiller",        unit="Kg",     current_stock=3.0,   reorder_level=1.0,  critical_level=0.5,  expiry_date="09.01.2026", quality="Good"),
    dict(name="Metro Mashed Potato",   category="Frozen",         department="kitchen", storage="Freezer",        unit="Kg",     current_stock=1.0,   reorder_level=1.0,  critical_level=1.0,  expiry_date=None,         quality="Good"),
    dict(name="Palak (Fresh)",         category="Vegetable",      department="kitchen", storage="Kitchen Chiller",unit="Kg",     current_stock=1.0,   reorder_level=1.0,  critical_level=0.5,  expiry_date="08.01.2026", quality="Good"),
    dict(name="Cauliflower",           category="Vegetable",      department="kitchen", storage="Kitchen",        unit="Pieces", current_stock=1.0,   reorder_level=6.0,  critical_level=4.0,  expiry_date=None,         quality="Good"),
    dict(name="Cucumber",              category="Vegetable",      department="kitchen", storage="Kitchen",        unit="Pieces", current_stock=2.0,   reorder_level=6.0,  critical_level=4.0,  expiry_date=None,         quality="Good"),
    dict(name="Curry Leaves",          category="Vegetable",      department="kitchen", storage="Kitchen",        unit="Pack",   current_stock=1.0,   reorder_level=5.0,  critical_level=3.0,  expiry_date=None,         quality="Good"),
    dict(name="Tomato",                category="Vegetable",      department="kitchen", storage="Kitchen",        unit="Kg",     current_stock=0.15,  reorder_level=4.0,  critical_level=2.0,  expiry_date=None,         quality="Good"),
    dict(name="Chicken",               category="Meat",           department="kitchen", storage="Chiller",        unit="Kg",     current_stock=2.9,   reorder_level=5.0,  critical_level=2.0,  expiry_date="09.01.2026", quality="Good"),
    dict(name="Mutton",                category="Meat",           department="kitchen", storage="Chiller",        unit="Kg",     current_stock=0.2,   reorder_level=2.0,  critical_level=1.0,  expiry_date=None,         quality="Good"),
    dict(name="Ginger",                category="Vegetable",      department="kitchen", storage="Kitchen Chiller",unit="Kg",     current_stock=9.0,   reorder_level=2.0,  critical_level=1.0,  expiry_date="10.01.2026", quality="Very Good"),
    dict(name="Garlic",                category="Vegetable",      department="kitchen", storage="Kitchen Chiller",unit="Kg",     current_stock=31.8,  reorder_level=5.0,  critical_level=2.0,  expiry_date="10.01.2026", quality="Very Good"),
    dict(name="Potato",                category="Vegetable",      department="kitchen", storage="Dry Store",      unit="Kg",     current_stock=10.0,  reorder_level=5.0,  critical_level=2.0,  expiry_date="10.01.2026", quality="Very Good"),
    dict(name="Onion",                 category="Vegetable",      department="kitchen", storage="Dry Store",      unit="Kg",     current_stock=8.0,   reorder_level=4.0,  critical_level=2.0,  expiry_date="28.12.2025", quality="Good"),
    # ── KITCHEN – Spices (27.01 sheet) ────────────────────────────────────────
    dict(name="Cumin Seeds",           category="Spice",          department="kitchen", storage="Spice Store",    unit="Kg",     current_stock=2.6,   reorder_level=0.5,  critical_level=0.2,  expiry_date="31.08.2026", quality="Good"),
    dict(name="Coriander Seeds",       category="Spice",          department="kitchen", storage="Spice Store",    unit="Kg",     current_stock=2.5,   reorder_level=0.5,  critical_level=0.2,  expiry_date="01.09.2027", quality="Good"),
    dict(name="Black Cardamom",        category="Spice",          department="kitchen", storage="Spice Store",    unit="Kg",     current_stock=0.99,  reorder_level=0.2,  critical_level=0.1,  expiry_date="01.04.2027", quality="Good"),
    dict(name="Star Anise",            category="Spice",          department="kitchen", storage="Spice Store",    unit="Kg",     current_stock=1.48,  reorder_level=0.2,  critical_level=0.1,  expiry_date="01.08.2027", quality="Good"),
    dict(name="Cloves",                category="Spice",          department="kitchen", storage="Spice Store",    unit="Kg",     current_stock=1.0,   reorder_level=0.2,  critical_level=0.1,  expiry_date=None,         quality="Good"),
    dict(name="Turmeric Powder",       category="Spice",          department="kitchen", storage="Spice Store",    unit="Kg",     current_stock=0.45,  reorder_level=0.2,  critical_level=0.1,  expiry_date=None,         quality="Good"),
    dict(name="Cardamom Powder",       category="Spice",          department="kitchen", storage="Spice Store",    unit="Kg",     current_stock=0.14,  reorder_level=0.1,  critical_level=0.05, expiry_date=None,         quality="Good"),
    # ── KITCHEN – Service Stock ────────────────────────────────────────────────
    dict(name="Ghee",                  category="Dairy",          department="kitchen", storage="Kitchen Shelf",  unit="Kg",     current_stock=2.0,   reorder_level=0.5,  critical_level=0.2,  expiry_date="01.10.2026", quality="Good"),
    dict(name="Mustard Oil",           category="Oil",            department="kitchen", storage="Kitchen Shelf",  unit="Ltr",    current_stock=3.0,   reorder_level=1.0,  critical_level=0.5,  expiry_date="01.04.2026", quality="Very Good"),
    dict(name="Corn Flour",            category="Dry Goods",      department="kitchen", storage="Kitchen Shelf",  unit="Kg",     current_stock=0.25,  reorder_level=0.2,  critical_level=0.1,  expiry_date=None,         quality="Good"),
    dict(name="Gram Flour (Besan)",    category="Dry Goods",      department="kitchen", storage="Kitchen Shelf",  unit="Kg",     current_stock=0.25,  reorder_level=0.2,  critical_level=0.1,  expiry_date=None,         quality="Good"),
    dict(name="Rose Water",            category="Extract",        department="kitchen", storage="Kitchen Shelf",  unit="Ml",     current_stock=50.0,  reorder_level=100.0,critical_level=50.0, expiry_date=None,         quality="Good"),
    dict(name="Kewra Water",           category="Extract",        department="kitchen", storage="Kitchen Shelf",  unit="Ml",     current_stock=150.0, reorder_level=100.0,critical_level=50.0, expiry_date=None,         quality="Good"),
    dict(name="Cashews",               category="Nuts",           department="kitchen", storage="Kitchen Shelf",  unit="Kg",     current_stock=0.6,   reorder_level=0.3,  critical_level=0.1,  expiry_date=None,         quality="Good"),
    dict(name="Walnuts",               category="Nuts",           department="kitchen", storage="Kitchen Shelf",  unit="Gms",    current_stock=200.0, reorder_level=100.0,critical_level=50.0, expiry_date="21.03.2026", quality="Good"),
    # ── BAR – Spirits & Wine (Bar_Inventory sheet) ────────────────────────────
    dict(name="Smirnoff",              category="Spirit",         department="bar",     storage="Bar",            unit="Ml",     current_stock=1900.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Absolut",               category="Spirit",         department="bar",     storage="Bar",            unit="Ml",     current_stock=250.0, reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Grey Goose",            category="Spirit",         department="bar",     storage="Bar",            unit="Ml",     current_stock=700.0, reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Gordons",               category="Gin",            department="bar",     storage="Bar",            unit="Ml",     current_stock=3000.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Gin Mare",              category="Gin",            department="bar",     storage="Bar",            unit="Ml",     current_stock=1750.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Bombay Sapphire",       category="Gin",            department="bar",     storage="Bar",            unit="Ml",     current_stock=1750.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Bacardi Carta Blanca",  category="Rum",            department="bar",     storage="Bar",            unit="Ml",     current_stock=3000.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Malibu White Rum",      category="Rum",            department="bar",     storage="Bar",            unit="Ml",     current_stock=700.0, reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Sierra",                category="Tequila",        department="bar",     storage="Bar",            unit="Ml",     current_stock=2000.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Aperol",                category="Aperitif",       department="bar",     storage="Bar",            unit="Ml",     current_stock=1000.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Aperol Small",          category="Aperitif",       department="bar",     storage="Bar",            unit="Ml",     current_stock=700.0, reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Ballantins Finest",     category="Whisky",         department="bar",     storage="Bar",            unit="Ml",     current_stock=2000.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Jim Beam Bourbon",      category="Whisky",         department="bar",     storage="Bar",            unit="Ml",     current_stock=2000.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Chianti",               category="Wine",           department="bar",     storage="Bar",            unit="Ml",     current_stock=4500.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
    dict(name="Bordeaux",              category="Wine",           department="bar",     storage="Bar",            unit="Ml",     current_stock=3000.0,reorder_level=750.0, critical_level=500.0,expiry_date=None,         quality="Good"),
]


def seed():
    create_tables()
    db = SessionLocal()
    if db.query(InventoryItem).count() > 0:
        db.close()
        return
    for d in SEED_DATA:
        db.add(InventoryItem(**d))
    db.commit()
    db.close()
    print(f"[Seed] {len(SEED_DATA)} items loaded from Rooh Gastronomy Excel data.")


if __name__ == "__main__":
    seed()
