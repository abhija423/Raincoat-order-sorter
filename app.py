import io
import re
from collections import defaultdict
import fitz  # PyMuPDF
import pypdf
import streamlit as st

st.set_page_config(
    page_title="Raincoat Order Sorter",
    page_icon="📦",
    layout="centered",
)

st.title("📦 Raincoat Order Sorting Engine")

# --- Compiled Regular Expressions (Compiled Once) ---
CUSTOMER_RE = re.compile(r"Customer Address\s*(.*?)\n", re.S | re.I)
BILL_RE = re.compile(r"BILL TO\s*/\s*SHIP TO(.*?)(?:Sold by)", re.S | re.I)
PIN_RE = re.compile(r"\b(\d{6})\b")
PHONE_RE = re.compile(r"\b([6-9]\d{9})\b")
FREE_SIZE_RE = re.compile(r"FREE SIZE\s+(\d+)", re.I)

EXCHANGE_PATTERNS = [
    re.compile(r"\bexchange order\b"),
    re.compile(r"\bexchange shipment\b"),
    re.compile(r"\breplacement order\b"),
    re.compile(r"\breplacement shipment\b"),
    re.compile(r"\breplacement\b"),
    re.compile(r"\bexchange\b"),
]

CUSTOMER_BLOCK_RE = re.compile(r"Customer Address[\s\S]*?If undelivered", re.I)
BILL_BLOCK_RE = re.compile(r"BILL TO\s*/\s*SHIP TO[\s\S]*", re.I)
SPACES_RE = re.compile(r"\s+")

# --- Dynamic Key & State Initialization ---
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

if "processed" not in st.session_state:
    st.session_state.processed = False

if "results" not in st.session_state:
    st.session_state.results = None


# --- Reset Engine Button ---
if st.button("🔄 Reset Engine", use_container_width=True):
    st.cache_data.clear()
    st.cache_resource.clear()
    
    st.session_state.processed = False
    st.session_state.results = None
    
    st.session_state.uploader_key += 1
    st.rerun()


# --- Dynamic Key File Uploader with Primary (Green) Button ---
if not st.session_state.processed:
    uploaded_files = st.file_uploader(
        "Upload one or more PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"pdf_uploader_{st.session_state.uploader_key}",
    )
    process_clicked = st.button(
        "🚀 Process PDFs",
        type="primary",
        use_container_width=True,
    )
else:
    uploaded_files = None
    process_clicked = False

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
    text = SPACES_RE.sub(" ", text)
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
                    m = FREE_SIZE_RE.search(product)
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
    m = CUSTOMER_RE.search(text)
    if m:
        name = m.group(1).strip()

    bill = ""
    m = BILL_RE.search(text)
    if m:
        bill = m.group(1)

    pin = ""
    p = PIN_RE.search(bill)
    if p:
        pin = p.group(1)

    phone = ""
    ph = PHONE_RE.search(text)
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

    customer_block = CUSTOMER_BLOCK_RE.search(work_text)
    if customer_block:
        work_text = work_text.replace(customer_block.group(0), "")

    bill_block = BILL_BLOCK_RE.search(work_text)
    if bill_block:
        work_text = work_text[:bill_block.start()]

    work_text = SPACES_RE.sub(" ", work_text).lower()

    for pattern in EXCHANGE_PATTERNS:
        if pattern.search(work_text):
            return True

    return False


def parse_pdf(pdf_bytes):
    # Pass B & A: Use PyMuPDF (fitz) directly for ultra-fast single-pass reading and extraction
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    progress = st.progress(0)
    total = len(doc)

    for idx in range(total):
        # Pass E: Update UI every 25 pages instead of every single page
        if idx % 25 == 0 or idx == total - 1:
            progress.progress((idx + 1) / total)
            
        page = doc.load_page(idx)
        text = page.get_text() or ""
        product = parse_product_table(text)
        customer = extract_customer(text)
        is_exchange = detect_exchange(text)

        # Pass C: Removed heavy "reader_page" reference object entirely
        pages.append(
            {
                "idx": idx,
                "page": idx + 1,
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
    doc.close()
    return pages


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
    # Pass G: Fast hash-map pre-grouping based on exact Name + PIN combinations
    bucket_groups = defaultdict(list)
    for page in all_pages:
        # Construct hashable compound key
        key = (page["identity"][0], page["identity"][2])
        bucket_groups[key].append(page)

    groups = []
    # Only run fuzzy address cross-matching within targeted structural collisions
    for identity_key, matched_pages in bucket_groups.items():
        for page in matched_pages:
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
    st.subheader("📊 Sorting Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Main PDF", len(main_pages))
    c2.metric("Duplicate PDF", len(duplicate_pages))
    c3.metric("Exchange", len(exchange_orders))
    c4.metric("Bulk Qty", len(bulk_orders))

    rows = []
    for new_page, page in enumerate(main_pages, start=1):
        if page["is_exchange"]:
            bucket = "Exchange"
        elif page["qty"] > 1:
            bucket = "Bulk Qty"
        else:
            bucket = "Normal"

        rows.append(
            {
                "Output": new_page,
                "Original": page["page"],
                "Customer": page["name"],
                "Color": page["color"],
                "Size": page["size"],
                "Qty": page["qty"],
                "Bucket": bucket,
            }
        )

    offset = len(main_pages)
    for i, page in enumerate(duplicate_pages, start=1):
        rows.append(
            {
                "Output": offset + i,
                "Original": page["page"],
                "Customer": page["name"],
                "Color": page["color"],
                "Size": page["size"],
                "Qty": page["qty"],
                "Bucket": "Duplicate",
            }
        )

    with st.expander("Page Movement"):
        st.dataframe(rows, hide_index=True, use_container_width=True)


def show_duplicate_groups(duplicate_groups):
    if not duplicate_groups:
        return
    st.subheader("Duplicate Customers")
    for group in duplicate_groups:
        first = group[0]
        pages = " , ".join(str(x["page"]) for x in group)
        st.markdown(
            f"**{first['name']}**\n\nPages : {pages}\n\nPIN : {first['pin']}\n\nOrders : {len(group)}"
        )


def generate_pdf(reader, final_pages):
    writer = pypdf.PdfWriter()
    progress = st.progress(0)
    total = len(final_pages)
    
    for i, page in enumerate(final_pages):
        # Pass F: Throttle progress rendering updates to every 50 loops to clear DOM overhead
        if i % 50 == 0 or i == total - 1:
            progress.progress((i + 1) / total)
        writer.add_page(reader.pages[page["idx"]])
        
    progress.empty()
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def generate_cropped_pdf(original_pdf_bytes, main_pages):
    source = fitz.open(stream=original_pdf_bytes, filetype="pdf")
    output = fitz.open()

    label_width = None
    label_height = None

    for page_info in main_pages:
        page = source.load_page(page_info["idx"])
        rect = page.rect

        order_box = page.search_for("Order No.")

        if order_box:
            keep_bottom = order_box[0].y1 + 40
        else:
            keep_bottom = rect.height

        clip = fitz.Rect(
            0,
            0,
            rect.width,
            keep_bottom,
        )

        if label_width is None:
            label_width = clip.width
            label_height = clip.height

        new_page = output.new_page(
            width=label_width,
            height=label_height,
        )

        new_page.show_pdf_page(
            new_page.rect,
            source,
            page.number,
            clip=clip,
        )

    pdf = output.tobytes()
    output.close()
    source.close()
    return io.BytesIO(pdf)


def show_exchange_summary(exchange_orders):
    if not exchange_orders:
        return
    size_summary = defaultdict(int)
    total_qty = 0
    for page in exchange_orders:
        qty = page["qty"]
        size = page["size"]
        size_summary[size] += qty
        total_qty += qty

    st.markdown("---")
    st.subheader("🔄 Exchange Summary")
    summary = []
    for size in ["S", "M", "L", "XL", "XXL", "XXXL", "FREE SIZE"]:
        summary.append({"Size": size, "Qty": size_summary[size]})
    st.table(summary)
    st.success(f"Total Exchange Quantity : {total_qty}")


def show_packing_summary(all_pages):
    summary = defaultdict(int)
    total_qty = 0
    for page in all_pages:
        color = page["color"].upper() if page["color"] else "UNKNOWN COLOR"
        size = page["size"].upper() if page["size"] else "UNKNOWN SIZE"
        qty = page["qty"]
        key = (color, size)
        summary[key] += qty
        total_qty += qty

    st.markdown("---")
    st.subheader("📦 Packing Summary Matrix (Main PDF)")
    colors = ["NAVY BLUE", "BLACK", "FREE SIZE"]
    sizes = ["S", "M", "L", "XL", "XXL", "XXXL", "FREE SIZE"]

    for color in colors:
        st.markdown(f"### {color}")
        rows = []
        subtotal = 0
        for size in sizes:
            qty = summary[(color, size)]
            subtotal += qty
            rows.append({"Size": size, "Qty": qty})
        
        unknown_qty = sum(v for k, v in summary.items() if k[0] == color and k[1] not in sizes)
        if unknown_qty > 0:
            rows.append({"Size": "OTHER/UNPARSED", "Qty": unknown_qty})
            subtotal += unknown_qty

        st.table(rows)
        st.success(f"Total {color} : {subtotal}")

    st.info(f"Grand Total Pieces : {total_qty}")


def show_parser_warnings(all_pages):
    warnings = []
    for page in all_pages:
        issues = []
        if not page["size"]:
            issues.append("Missing/Unparsed Size")
        if not page["color"]:
            issues.append("Missing/Unparsed Color")
        if not page["sku"]:
            issues.append("Unknown/Missing SKU")
        if page["qty"] <= 0:
            issues.append(f"Invalid Quantity ({page['qty']})")
        
        if issues:
            warnings.append({
                "Page": page["page"],
                "Customer": page["name"],
                "Issues Found": ", ".join(issues)
            })
            
    if warnings:
        st.markdown("---")
        st.warning("⚠️ Parser Warnings (Verify these labels manually before printing)")
        st.dataframe(warnings, hide_index=True, use_container_width=True)


# Processing block waiting for files + custom action button click
if uploaded_files and process_clicked:
    with st.spinner("Merging uploaded files..."):
        combined_writer = pypdf.PdfWriter()
        for uploaded_file in uploaded_files:
            reader = pypdf.PdfReader(uploaded_file)
            for page in reader.pages:
                combined_writer.add_page(page)
        
        buffer = io.BytesIO()
        combined_writer.write(buffer)
        buffer.seek(0)
        file_bytes = buffer.getvalue()

    with st.spinner("Reading combined PDF via PyMuPDF..."):
        all_pages = parse_pdf(file_bytes)

    (
        main_pages,
        duplicate_pages,
        normal_orders,
        exchange_orders,
        bulk_orders,
        duplicate_groups,
    ) = build_final_order(all_pages)

    with st.spinner("Generating Final Output PDFs..."):
        # Instantiated once for underlying page index copy writes
        underlying_reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        main_pdf = generate_pdf(underlying_reader, main_pages)
        duplicate_pdf = generate_pdf(underlying_reader, duplicate_pages)
        cropped_pdf = generate_cropped_pdf(file_bytes, main_pages)

    st.session_state.results = {
        "main": main_pdf.getvalue(),
        "duplicate": duplicate_pdf.getvalue(),
        "cropped": cropped_pdf.getvalue(),
        "summary": (
            main_pages,
            duplicate_pages,
            exchange_orders,
            bulk_orders,
            duplicate_groups,
            all_pages,
        ),
    }
    st.session_state.processed = True
    st.rerun()


# Processing Finished UI State
if st.session_state.processed:
    (
        main_pages,
        duplicate_pages,
        exchange_orders,
        bulk_orders,
        duplicate_groups,
        all_pages,
    ) = st.session_state.results["summary"]

    st.success(
        f"Processed {len(all_pages)} Pages\n\n"
        f"Main PDF : {len(main_pages)} Pages\n\n"
        f"Duplicate PDF : {len(duplicate_pages)} Pages"
    )

    st.markdown("---")
    st.subheader("📥 Download PDFs")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.download_button(
            "📄 Main PDF",
            st.session_state.results["main"],
            "Sorted_Main.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with col2:
        st.download_button(
            "👥 Duplicate PDF",
            st.session_state.results["duplicate"],
            "Duplicate_Orders.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with col3:
        st.download_button(
            "✂️ Cropped PDF",
            st.session_state.results["cropped"],
            "Cropped_Main.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    show_debug_table(main_pages, duplicate_pages, exchange_orders, bulk_orders)
    show_exchange_summary(exchange_orders)
    show_packing_summary(main_pages)
    show_parser_warnings(all_pages)

    if duplicate_pages:
        show_duplicate_groups(duplicate_groups)
        
    st.markdown("---")
    st.success("Done ✅")
