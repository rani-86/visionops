"""Synthetic document generator.

Used for three things:
  * training text for the classifier (with OCR-style noise),
  * rendered images for the load test (real OCR runs on them in the worker),
  * fixtures for unit tests.

Everything is driven by an explicit ``random.Random`` so runs are reproducible.
"""

from __future__ import annotations

import io
import random
import string

LABELS = ("invoice", "receipt", "bank_statement", "payslip", "id_card")

FIRST = ["Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Meera", "Arjun", "Sneha", "Kabir", "Isha",
         "Rahul", "Divya", "Karan", "Pooja", "Nikhil", "Asha", "Sameer", "Neha", "Aditya", "Riya"]
LAST = ["Sharma", "Iyer", "Patel", "Reddy", "Nair", "Gupta", "Singh", "Menon", "Das", "Kulkarni",
        "Joshi", "Rao", "Bose", "Verma", "Pillai", "Chopra"]
COMPANIES = ["Nimbus Tech Pvt Ltd", "Orchid Traders", "BlueRiver Logistics", "Sapphire Foods",
             "Kestrel Analytics", "Lotus Hardware", "Monsoon Media LLP", "Indigo Textiles",
             "Pinnacle Pharma", "Granite Infra Ltd", "Saffron Retail", "Vertex Systems"]
BANKS = ["State Union Bank", "Coastal Co-operative Bank", "Metro Finance Bank", "Heritage Bank",
         "Northgate Bank"]
CITIES = ["Mumbai", "Pune", "Bengaluru", "Chennai", "Hyderabad", "Kolkata", "Delhi", "Jaipur", "Kochi"]
ITEMS = ["A4 Paper Ream", "Laser Toner", "USB-C Cable", "Office Chair", "Desk Lamp", "Notebook",
         "Wireless Mouse", "Monitor Stand", "Stapler", "Printer Ink", "Router", "Keyboard"]
STORE_ITEMS = ["Milk 1L", "Bread", "Eggs x12", "Rice 5kg", "Coffee", "Bananas", "Paneer", "Soap",
               "Shampoo", "Tea 500g", "Sugar 1kg", "Biscuits"]
TXN = ["UPI/PAYTM", "NEFT SALARY", "ATM WDL", "POS GROCERY", "IMPS TRANSFER", "ECS EMI",
       "UPI/GPAY", "INTEREST CREDIT", "CHQ DEPOSIT", "BILL PAY ELECTRICITY"]


def _name(r: random.Random) -> str:
    return f"{r.choice(FIRST)} {r.choice(LAST)}"


def _date(r: random.Random) -> str:
    return f"{r.randint(1, 28):02d}/{r.randint(1, 12):02d}/{r.randint(2022, 2026)}"


def _amount(r: random.Random, lo: float, hi: float) -> float:
    return round(r.uniform(lo, hi), 2)


def _invoice(r: random.Random) -> list[str]:
    lines = [r.choice(["TAX INVOICE", "INVOICE", "Invoice"]), r.choice(COMPANIES),
             f"{r.choice(CITIES)} GSTIN 27{''.join(r.choices(string.ascii_uppercase + string.digits, k=13))}",
             f"Invoice No: INV-{r.randint(1000, 99999)}", f"Invoice Date: {_date(r)}",
             f"Due Date: {_date(r)}", f"Bill To: {_name(r)}", "Description  Qty  Rate  Amount"]
    subtotal = 0.0
    for _ in range(r.randint(2, 5)):
        qty, rate = r.randint(1, 10), _amount(r, 50, 5000)
        subtotal += qty * rate
        lines.append(f"{r.choice(ITEMS)}  {qty}  {rate:.2f}  {qty * rate:.2f}")
    tax = round(subtotal * 0.18, 2)
    lines += [f"Subtotal {subtotal:.2f}", f"GST 18% {tax:.2f}", f"Total Amount Due {subtotal + tax:.2f}",
              "Payment terms: Net 30"]
    return lines


def _receipt(r: random.Random) -> list[str]:
    store = r.choice(["FreshMart", "DailyNeeds", "Green Basket", "QuickShop", "Corner Store"])
    lines = [store, f"{r.choice(CITIES)}", f"Receipt #{r.randint(100000, 999999)}",
             f"Date: {_date(r)} Time: {r.randint(8, 22):02d}:{r.randint(0, 59):02d}", "Cashier: " + r.choice(FIRST)]
    total = 0.0
    for _ in range(r.randint(3, 7)):
        price = _amount(r, 10, 600)
        total += price
        lines.append(f"{r.choice(STORE_ITEMS)}  {price:.2f}")
    lines += [f"TOTAL {total:.2f}", f"PAID {r.choice(['CASH', 'CARD', 'UPI'])} {total:.2f}",
              "Thank you for shopping!", "No returns without receipt"]
    return lines


def _bank_statement(r: random.Random) -> list[str]:
    bal = _amount(r, 5000, 200000)
    lines = [r.choice(BANKS), "ACCOUNT STATEMENT", f"Account Holder: {_name(r)}",
             f"Account Number: XXXXXX{r.randint(1000, 9999)}", f"IFSC: {''.join(r.choices(string.ascii_uppercase, k=4))}0{r.randint(100000, 999999)}",
             f"Statement Period: {_date(r)} to {_date(r)}", f"Opening Balance {bal:.2f}",
             "Date  Narration  Debit  Credit  Balance"]
    for _ in range(r.randint(4, 8)):
        amt = _amount(r, 100, 25000)
        if r.random() < 0.5:
            bal -= amt
            lines.append(f"{_date(r)}  {r.choice(TXN)}  {amt:.2f}  -  {bal:.2f}")
        else:
            bal += amt
            lines.append(f"{_date(r)}  {r.choice(TXN)}  -  {amt:.2f}  {bal:.2f}")
    lines.append(f"Closing Balance {bal:.2f}")
    return lines


def _payslip(r: random.Random) -> list[str]:
    basic = _amount(r, 20000, 150000)
    hra, special = round(basic * 0.4, 2), _amount(r, 2000, 30000)
    pf, tax = round(basic * 0.12, 2), _amount(r, 0, 20000)
    gross = basic + hra + special
    return [r.choice(COMPANIES), r.choice(["PAYSLIP", "Salary Slip", "PAY SLIP"]),
            f"Pay Period: {r.choice(['January', 'February', 'March', 'April', 'May', 'June', 'July'])} {r.randint(2022, 2026)}",
            f"Employee Name: {_name(r)}", f"Employee ID: EMP{r.randint(1000, 9999)}",
            f"Designation: {r.choice(['Analyst', 'Engineer', 'Manager', 'Associate', 'Consultant'])}",
            "Earnings  Amount", f"Basic Salary {basic:.2f}", f"House Rent Allowance {hra:.2f}",
            f"Special Allowance {special:.2f}", f"Gross Earnings {gross:.2f}", "Deductions  Amount",
            f"Provident Fund {pf:.2f}", "Professional Tax 200.00", f"Income Tax TDS {tax:.2f}",
            f"Net Pay {gross - pf - tax - 200:.2f}"]


def _id_card(r: random.Random) -> list[str]:
    return [r.choice(["INCOME TAX DEPARTMENT", "GOVT OF INDIA", "Permanent Account Number Card"]),
            "Permanent Account Number", f"{''.join(r.choices(string.ascii_uppercase, k=5))}{r.randint(1000, 9999)}{r.choice(string.ascii_uppercase)}",
            f"Name {_name(r).upper()}", f"Father's Name {_name(r).upper()}", f"Date of Birth {_date(r)}",
            "Signature"]


_GENERATORS = {"invoice": _invoice, "receipt": _receipt, "bank_statement": _bank_statement,
               "payslip": _payslip, "id_card": _id_card}

_OCR_CONFUSIONS = {"0": "O", "O": "0", "1": "l", "l": "1", "5": "S", "S": "5", "8": "B", "B": "8",
                   "rn": "m", "m": "rn", "e": "c", "I": "|", "g": "q"}


def generate_lines(label: str, r: random.Random) -> list[str]:
    return _GENERATORS[label](r)


def generate_text(label: str, r: random.Random) -> str:
    return "\n".join(generate_lines(label, r))


def add_ocr_noise(text: str, r: random.Random, rate: float = 0.03) -> str:
    """Simulate OCR mistakes: character confusions, drops and merged/split words."""
    out: list[str] = []
    i = 0
    while i < len(text):
        two = text[i:i + 2]
        if two in _OCR_CONFUSIONS and r.random() < rate:
            out.append(_OCR_CONFUSIONS[two])
            i += 2
            continue
        ch = text[i]
        roll = r.random()
        if roll < rate * 0.3:
            pass  # dropped character
        elif roll < rate and ch in _OCR_CONFUSIONS:
            out.append(_OCR_CONFUSIONS[ch])
        elif roll < rate * 1.2 and ch == " ":
            pass  # merged words
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def make_dataset(n_per_class: int, seed: int, noise_rate: float = 0.03) -> tuple[list[str], list[str]]:
    r = random.Random(seed)
    texts, labels = [], []
    for label in LABELS:
        for _ in range(n_per_class):
            texts.append(add_ocr_noise(generate_text(label, r), r, noise_rate))
            labels.append(label)
    return texts, labels


def render_png(lines: list[str], r: random.Random, degrade: bool = True) -> bytes:
    """Render text lines as a scanned-looking PNG (white page, dark text, mild rotation/noise)."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    font = ImageFont.load_default(size=22)
    width = 1100
    height = 80 + 34 * len(lines)
    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)
    y = 40
    for line in lines:
        draw.text((50, y), line, fill=r.randint(0, 50), font=font)
        y += 34
    if degrade:
        img = img.rotate(r.uniform(-1.5, 1.5), expand=True, fillcolor=255)
        if r.random() < 0.5:
            img = img.filter(ImageFilter.GaussianBlur(radius=r.uniform(0.3, 0.8)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
