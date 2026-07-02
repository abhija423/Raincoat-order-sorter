import io
import re
import time
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

# --- 🚀 7. Green Styling Injection ---
st.markdown("""
<style>
div.stButton > button:first-child{
    background:#1DB954;
    color:white;
    font-weight:700;
    border-radius:10px;
    height:48px;
    border: none;
}
div.stButton > button:first-child:hover{
    background:#18a64c;
    color:white;
}
</style>
""", unsafe_allow_html=True)

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


# --- Dynamic Key File Uploader ---
if not st.session_state.processed:
    uploaded_files = st.file_uploader(
        "Upload one or more PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"pdf_uploader_{st.session_state.uploader_key}",
    )
    process_clicked = st.button(
        "🚀 Process PDFs",
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
        "india", "district", "state", "near", "opp", 
        "opposite", "landmark", "po", "post", "ps", "police", "station"
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
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        if "SKU" in line and "Size" in line and "Qty" in line and "Color" in line:
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

            return {"sku": sku, "size": size, "qty": qty, "color": color, "order": order}

    return {"sku": "", "size": "", "qty": 1, "color": "", "order": ""}


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
    return {"name": name, "address": address, "pin": pin, "phone": phone, "identity": identity}


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


def parse_pdf(pdf_bytes, progress, status):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    total = len(doc)

    for idx in range(total):
        if idx % 20 == 0 or idx == total - 1:
            progress.progress((idx + 1) / total)
            status.markdown(f"### 🔍 Extracting Document Text\n**Analyzed:** {idx+1:,} / {total:,} pages")
            
        page = doc.load_page(idx)
        text = page.get_text() or ""
        product = parse_product_table(text)
        customer = extract_customer(text)
        is_exchange = detect_exchange(text)

        pages.append({
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
        })
    doc.close()
    return pages


def split_orders(all_pages):
    bucket_groups = defaultdict(list)
    for page in all_pages:
        key = (page["identity"][0], page["identity"][2])
        bucket_groups[key].append(page)

    groups = []
    for identity_key, matched_pages in bucket_groups.items():
        for page in matched_pages:
            found = False
            for group in groups:
                if identity_key == (group[0]["identity"][0], group[0]["identity"][2]):
                    addr1 = page["identity"][1]
                    addr2 = group[0]["identity"][1]
                    words1, words2 = set(addr1.split()), set(addr2.split())
                    common = len(words1 & words2)
                    smaller = min(len(words1), len(words2))
                    
                    if smaller > 0 and (common / smaller) >= 0.85:
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

    duplicate_order_groups.sort(key=lambda g: (g[0]["name"].lower(), g[0]["page"]))
    return normal_orders, exchange_orders, bulk_orders, duplicate_order_groups


def build_final_order(all_pages):
    normal_orders, exchange_orders, bulk_orders, duplicate_groups = split_orders(all_pages)

    normal_sorted = sorted(normal_orders, key=lambda x: (x["color_rank"], x["size_rank"], x["page"]))
    exchange_sorted = sorted(exchange_orders, key=lambda x: (x["color_rank"], x["size_rank"], x["page"]))
    bulk_sorted = sorted(bulk_orders, key=lambda x: (x["color_rank"], x["size_rank"], x["page"]))
    
    duplicate_sorted = []
    for group in duplicate_groups:
        group.sort(key=lambda x: x["page"])
        duplicate_sorted.extend(group)

    main_pdf_pages = normal_sorted + exchange_sorted + bulk_sorted
    return main_pdf_pages, duplicate_sorted, normal_sorted, exchange_orders, bulk_sorted, duplicate_groups


def show_debug_table(main_pages, duplicate_pages, exchange_orders, bulk_orders):
    st.subheader("📊 Sorting Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Main PDF", len(main_pages))
    c2.metric("Duplicate PDF", len(duplicate_pages))
    c3.metric("Exchange", len(exchange_orders))
    c4.metric("Bulk Qty", len(bulk_orders))

    rows = []
    for new_page, page in enumerate(main_pages, start=1):
        bucket = "Exchange" if page["is_exchange"] else ("Bulk Qty" if page["qty"] > 1 else "Normal")
        rows.append({
            "Output": new_page, "Original": page["page"], "Customer": page["name"],
            "Color": page["color"], "Size": page["size"], "Qty": page["qty"], "Bucket": bucket
        })

    offset = len(main_pages)
    for i, page in enumerate(duplicate_pages, start=1):
        rows.append({
            "Output": offset + i, "Original": page["page"], "Customer": page["name"],
            "Color": page["color"], "Size": page["size"], "Qty": page["qty"], "Bucket": "Duplicate"
        })

    with st.expander("Page Movement"):
        st.dataframe(rows, hide_index=True, use_container_width=True)


def show_duplicate_groups(duplicate_groups):
    if not duplicate_groups:
        return
    st.subheader("Duplicate Customers")
    for group in duplicate_groups:
        first = group[0]
        pages = " , ".join(str(x["page"]) for x in group)
        st.markdown(f"**{first['name']}**\n\nPages : {pages}\n\nPIN : {first['pin']}\n\nOrders : {len(group)}")


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
    summary = [{"Size": size, "Qty": size_summary[size]} for size in ["S", "M", "L", "XL", "XXL", "XXXL", "FREE SIZE"]]
    st.table(summary)
    st.success(f"Total Exchange Quantity : {total_qty}")


def show_packing_summary(all_pages):
    summary = defaultdict(int)
    total_qty = 0
    for page in all_pages:
        color = page["color"].upper() if page["color"] else "UNKNOWN COLOR"
        size = page["size"].upper() if page["size"] else "UNKNOWN SIZE"
        qty = page["qty"]
        summary[(color, size)] += qty
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
        if not page["size"]: issues.append("Missing/Unparsed Size")
        if not page["color"]: issues.append("Missing/Unparsed Color")
        if not page["sku"]: issues.append("Unknown/Missing SKU")
        if page["qty"] <= 0: issues.append(f"Invalid Quantity ({page['qty']})")
        
        if issues:
            warnings.append({"Page": page["page"], "Customer": page["name"], "Issues Found": ", ".join(issues)})
            
    if warnings:
        st.markdown("---")
        st.warning("⚠️ Parser Warnings (Verify these labels manually before printing)")
        st.dataframe(warnings, hide_index=True, use_container_width=True)


# --- Core Process Execution Engine ---
if uploaded_files and process_clicked:
    progress = st.progress(0)
    status = st.empty()
    eta = st.empty()

    status.markdown("### 🧬 Initializing Processes\nMerging targets and creating standard temporary buffer...")
    
    combined_writer = pypdf.PdfWriter()
    for uploaded_file in uploaded_files:
        reader = pypdf.PdfReader(uploaded_file)
        for page in reader.pages:
            combined_writer.add_page(page)
    
    buffer = io.BytesIO()
    combined_writer.write(buffer)
    buffer.seek(0)
    file_bytes = buffer.getvalue()

    all_pages = parse_pdf(file_bytes, progress, status)
    main_pages, duplicate_pages, normal_orders, exchange_orders, bulk_orders, duplicate_groups = build_final_order(all_pages)

    underlying_reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    
    main_writer = pypdf.PdfWriter()
    dup_writer = pypdf.PdfWriter()
    
    total_compilation = len(main_pages) + len(duplicate_pages)
    start_compilation = time.perf_counter()
    
    for i, page in enumerate(main_pages):
        if i % 20 == 0 or i == len(main_pages) - 1:
            percent = (i + 1) / total_compilation
            progress.progress(percent)
            status.markdown(
                f"### 📄 Generating Main PDF\n"
                f"**Processed:** {i+1:,} / {len(main_pages):,} pages"
            )
            elapsed = time.perf_counter() - start_compilation
            avg = elapsed / (i + 1) if i > 0 else elapsed
            remaining = avg * (total_compilation - i - 1)
            mins, secs = int(remaining // 60), int(remaining % 60)
            eta.markdown(f"⏱ Estimated time remaining: **{mins}m {secs}s**")
            
        main_writer.add_page(underlying_reader.pages[page["idx"]])

    offset = len(main_pages)
    for i, page in enumerate(duplicate_pages):
        current_idx = offset + i
        if i % 20 == 0 or i == len(duplicate_pages) - 1:
            percent = (current_idx + 1) / total_compilation
            progress.progress(percent)
            status.markdown(
                f"### 👥 Generating Duplicate PDF\n"
                f"**Processed:** {i+1:,} / {len(duplicate_pages):,} pages"
            )
            elapsed = time.perf_counter() - start_compilation
            avg = elapsed / (current_idx + 1)
            remaining = avg * (total_compilation - current_idx - 1)
            mins, secs = int(remaining // 60), int(remaining % 60)
            eta.markdown(f"⏱ Estimated time remaining: **{mins}m {secs}s**")
            
        dup_writer.add_page(underlying_reader.pages[page["idx"]])

    main_buffer = io.BytesIO()
    main_writer.write(main_buffer)
    
    dup_buffer = io.BytesIO()
    dup_writer.write(dup_buffer)

    source_fitz = fitz.open(stream=file_bytes, filetype="pdf")
    output_fitz = fitz.open()

    total_crop = len(main_pages)
    start_crop = time.perf_counter()
    label_width, label_height = None, None

    for idx, page_info in enumerate(main_pages):
        if idx % 20 == 0 or idx == total_crop - 1:
            percent = (idx + 1) / total_crop
            progress.progress(percent)
            status.markdown(
                f"### ✂️ Generating Cropped PDF\n"
                f"**Processed:** {idx+1:,} / {total_crop:,} pages"
            )
            elapsed = time.perf_counter() - start_crop
            avg = elapsed / (idx + 1) if idx > 0 else elapsed
            remaining = avg * (total_crop - idx - 1)
            mins, secs = int(remaining // 60), int(remaining % 60)
            eta.markdown(f"⏱ Estimated time remaining: **{mins}m {secs}s**")

        page = source_fitz.load_page(page_info["idx"])
        rect = page.rect
        order_box = page.search_for("Order No.")
        keep_bottom = order_box[0].y1 + 40 if order_box else rect.height
        clip = fitz.Rect(0, 0, rect.width, keep_bottom)

        if label_width is None:
            label_width, label_height = clip.width, clip.height

        new_page = output_fitz.new_page(width=label_width, height=label_height)
        new_page.show_pdf_page(new_page.rect, source_fitz, page.number, clip=clip)

    cropped_buffer = io.BytesIO(output_fitz.tobytes())
    output_fitz.close()
    source_fitz.close()

    progress.empty()
    status.empty()
    eta.empty()

    st.session_state.results = {
        "main": main_buffer.getvalue(),
        "duplicate": dup_buffer.getvalue(),
        "cropped": cropped_buffer.getvalue(),
        "summary": (main_pages, duplicate_pages, exchange_orders, bulk_orders, duplicate_groups, all_pages),
    }
    st.session_state.processed = True
    st.rerun()


# --- Render Active Outputs ---
if st.session_state.processed:
    main_pages, duplicate_pages, exchange_orders, bulk_orders, duplicate_groups, all_pages = st.session_state.results["summary"]

    st.success(
        f"Processed {len(all_pages)} Pages\n\n"
        f"Main PDF : {len(main_pages)} Pages\n\n"
        f"Duplicate PDF : {len(duplicate_pages)} Pages"
    )

    st.markdown("---")
    st.subheader("📥 Download PDFs")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.download_button("📄 Main PDF", st.session_state.results["main"], "Sorted_Main.pdf", mime="application/pdf", use_container_width=True)
    with col2:
        st.download_button("👥 Duplicate PDF", st.session_state.results["duplicate"], "Duplicate_Orders.pdf", mime="application/pdf", use_container_width=True)
    with col3:
        st.download_button("✂️ Cropped PDF", st.session_state.results["cropped"], "Cropped_Main.pdf", mime="application/pdf", use_container_width=True)

    show_debug_table(main_pages, duplicate_pages, exchange_orders, bulk_orders)
    show_exchange_summary(exchange_orders)
    show_packing_summary(main_pages)
    show_parser_warnings(all_pages)

    if duplicate_pages:
        show_duplicate_groups(duplicate_groups)
        
    st.markdown("---")
    st.success("Done ✅")
