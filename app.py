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
    m = re.search(r"Customer
