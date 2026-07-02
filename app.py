import io
import re
from collections import defaultdict
import fitz
import pypdf
import streamlit as st

st.set_page_config(
    page_title="Raincoat Order Sorter",
    page_icon="📦",
    layout="centered",
)

st.title("📦 Raincoat Order Sorting Engine")

uploaded_files = st.file_uploader(
    "Upload one or more PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

SIZE_RANK = {
    "S": 1,
    "M": 2,
    "L": 3,
    "XL": 4,
    "XXL": 5,
    "XXXL": 6,
    "FREE SIZE": 7,
}


def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = text.replace("-", " ")
    text = text.replace(",", " ")
    text = text.replace("/", " ")
    text = text.replace(".", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def create_address_fingerprint(address):
    address = normalize(address)
    remove_words = {
        "india",
        "district",
        "state",
        "near",
        "opp",
        "opposite",
        "landmark",
        "po",
        "post",
        "ps",
        "police",
        "station",
    }
    words = []
    for word in address.split():
        if word in remove_words:
            continue
        words.append(word)
    return " ".join(words)


def get_size_rank(size):
    size = size.upper().strip()
    if size in SIZE_RANK:
        return SIZE_RANK[size]
    return 99


def get_color_rank(color):
    color = color.lower()
    if "navy" in color:
        return 1
    if "black" in color:
        return 2
    if "free" in color:
        return 3
    return 99


def parse_product_table(text):
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            lines.append(line)

    for i, line in enumerate(lines):
        if (
            "SKU" in line
            and "Size" in line
            and "Qty" in line
            and "Color" in line
        ):
            if i + 1 >= len(lines):
                break

            product = lines[i + 1]
            tokens = product.split()
            if len(tokens) < 5:
                continue

            sku = tokens[0]
            size = ""
            qty = 1
            color = ""
            order = ""

            for j, token in enumerate(tokens):
                upper = token.upper()
                if upper in ("S", "M", "L", "XL", "XXL", "XXXL"):
                    size = upper
                    if j + 1 < len(tokens):
                        try:
                            qty = int(tokens[j + 1])
                        except:
                            qty = 1

                    if j + 2 < len(tokens):
                        if tokens[j + 2].lower() == "navy":
                            color = "Navy Blue"
                            order = tokens[-1]
                        elif tokens[j + 2].lower() == "black":
                            color = "Black"
                            order = tokens[-1]
                    break

            if not size:
                if "FREE SIZE" in product.upper():
                    size = "FREE SIZE"
                    m = re.search(r"FREE SIZE\s+(\d+)", product, re.I)
                    if m:
                        qty = int(m.group(1))

                    if "NAVY" in product.upper():
                        color = "Navy Blue"
                    elif "BLACK" in product.upper():
                        color = "Black"
                    order = tokens[-1]

            return {
                "sku": sku,
                "size": size,
                "qty": qty,
                "color": color,
                "order": order,
            }

    return {
        "sku": "",
        "size": "",
        "qty": 1,
        "color": "",
        "order": "",
    }


def extract_customer(text):
    name = "Unknown"
    m = re.search(r"Customer Address\s*(.*?)\n", text, re.S | re.I)
    if m:
        name = m.group(1).strip()

    bill = ""
    m = re.search(r"BILL TO\s*/\s*SHIP TO(.*?)(?:Sold by)", text, re.S | re.I)
    if m:
        bill = m.group(1)

    pin = ""
    p = re.search(r"\b(\d{6})\b", bill)
    if p:
        pin = p.group(1)

    phone = ""
    ph = re.search(r"\b([6-9]\d{9})\b", text)
    if ph:
        phone = ph.group(1)

    address = create_address_fingerprint(bill)
    identity = (normalize(name), address, pin)
    return {
        "name": name,
        "address": address,
        "pin": pin,
        "phone": phone,
        "identity": identity,
    }


def detect_exchange(text):
    work_text = text

    customer_block = re.search(r"Customer Address[\s\S]*?If undelivered", work_text, re.I)
    if customer_block:
        work_text = work_text.replace(customer_block.group(0), "")

    bill_block = re.search(r"BILL TO\s*/\s*SHIP TO[\s\S]*", work_text, re.I)
    if bill_block:
        work_text = work_text[:bill_block.start()]

    work_text = re.sub(r"\s+", " ", work_text).lower()

    exchange_patterns = [
        r"\bexchange order\b",
        r"\bexchange shipment\b",
        r"\breplacement order\b",
        r"\breplacement shipment\b",
        r"\breplacement\b",
        r"\bexchange\b",
    ]

    for pattern in exchange_patterns:
        if re.search(pattern, work_text):
            return True

    return False


def parse_pdf(pdf_bytes):
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    progress = st.progress(0)
    total = len(reader.pages)

    for idx, page in enumerate(reader.pages):
        progress.progress((idx + 1) / total)
        text = page.extract_text() or ""
        product = parse_product_table(text)
        customer = extract_customer(text)
        is_exchange = detect_exchange(text)

        pages.append(
            {
                "idx": idx,
                "page": idx + 1,
                "reader_page": page,
                "text": text,
                "name": customer["name"],
                "identity": customer["identity"],
                "address": customer["address"],
                "pin": customer["pin"],
                "phone": customer["phone"],
                "sku": product["sku"],
                "qty": product["qty"],
                "size": product["size"],
                "size_rank": get_size_rank(product["size"]),
                "color": product["color"],
                "color_rank": get_color_rank(product["color"]),
                "order": product["order"],
                "is_exchange": is_exchange,
            }
        )
    progress.empty()
    return reader, pages


def same_customer(identity1, identity2):
    if identity1[0] != identity2[0]:
        return False
    if identity1[2] != identity2[2]:
        return False

    addr1 = identity1[1]
    addr2 = identity2[1]

    words1 = set(addr1.split())
    words2 = set(addr2.split())

    common = len(words1 & words2)
    smaller = min(len(words1), len(words2))

    if smaller == 0:
        return False

    similarity = common / smaller
    return similarity >= 0.85


def split_orders(all_pages):
    groups = []
    for page in all_pages:
        found = False
        for group in groups:
            if same_customer(page["identity"], group[0]["identity"]):
                group.append(page)
                found = True
                break
        if not found:
            groups.append([page])

    normal_orders = []
    exchange_orders = []
    bulk_orders = []
    duplicate_order_groups = []

    for group in groups:
        if len(group) > 1:
            group.sort(key=lambda x: x["page"])
            duplicate_order_groups.append(group)
            continue

        page = group[0]
        if page["is_exchange"]:
            exchange_orders.append(page)
        elif page["qty"] > 1:
            bulk_orders.append(page)
        else:
            normal_orders.append(page)

    duplicate_order_groups.sort(
        key=lambda g: (g[0]["name"].lower(), g[0]["page"])
    )
    return normal_orders, exchange_orders, bulk_orders, duplicate_order_groups


def sort_normal_orders(orders):
    return sorted(
        orders, key=lambda x: (x["color_rank"], x["size_rank"], x["page"])
    )


def sort_bulk_orders(orders):
    return sorted(
        orders, key=lambda x: (x["color_rank"], x["size_rank"], x["page"])
    )


def flatten_duplicate_groups(groups):
    final = []
    for group in groups:
        group.sort(key=lambda x: x["page"])
        final.extend(group)
    return final


def build_final_order(all_pages):
    (
        normal_orders,
        exchange_orders,
        bulk_orders,
        duplicate_groups,
    ) = split_orders(all_pages)

    normal_sorted = sort_normal_orders(normal_orders)
    exchange_sorted = sort_normal_orders(exchange_orders)
    bulk_sorted = sort_bulk_orders(bulk_orders)
    duplicate_sorted = flatten_duplicate_groups(duplicate_groups)

    main_pdf_pages = normal_sorted + exchange_sorted + bulk_sorted
    return (
        main_pdf_pages,
        duplicate_sorted,
        normal_sorted,
        exchange_sorted,
        bulk_sorted,
        duplicate_groups,
    )


def show_debug_table(main_pages, duplicate_pages, exchange_orders, bulk_orders):
    st.subheader("Sorting Summary")
    st.write(f"Main PDF Pages : {len(main_pages)}")
