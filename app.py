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

# --- SESSION STATE INITIALIZATION ---
if "processed" not in st.session_state:
    st.session_state.processed = False
if "processing_triggered" not in st.session_state:
    st.session_state.processing_triggered = False
if "main_pdf_data" not in st.session_state:
    st.session_state.main_pdf_data = None
if "duplicate_pdf_data" not in st.session_state:
    st.session_state.duplicate_pdf_data = None
if "cropped_pdf_data" not in st.session_state:
    st.session_state.cropped_pdf_data = None
if "all_pages" not in st.session_state:
    st.session_state.all_pages = []
if "main_pages" not in st.session_state:
    st.session_state.main_pages = []
if "duplicate_pages" not in st.session_state:
    st.session_state.duplicate_pages = []
if "exchange_orders" not in st.session_state:
    st.session_state.exchange_orders = []
if "bulk_orders" not in st.session_state:
    st.session_state.bulk_orders = []
if "duplicate_groups" not in st.session_state:
    st.session_state.duplicate_groups = []
if "last_uploaded_fingerprint" not in st.session_state:
    st.session_state.last_uploaded_fingerprint = None
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0


# Reset utility to completely clear state data and file uploader
def reset_application_state():
    st.session_state.processed = False
    st.session_state.processing_triggered = False
    st.session_state.main_pdf_data = None
    st.session_state.duplicate_pdf_data = None
    st.session_state.cropped_pdf_data = None
    st.session_state.all_pages = []
    st.session_state.main_pages = []
    st.session_state.duplicate_pages = []
    st.session_state.exchange_orders = []
    st.session_state.bulk_orders = []
    st.session_state.duplicate_groups = []
    st.session_state.last_uploaded_fingerprint = None

    # Force Streamlit to recreate the uploader (removes all uploaded PDFs)
    st.session_state.uploader_key += 1
    st.session_state.trigger_reset = False


# Check if reset was requested before rendering the file uploader
if st.session_state.get("trigger_reset", False):
    reset_application_state()
    st.rerun()

uploaded_files = st.file_uploader(
    "Upload one or more PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    key=f"pdf_uploader_file_input_{st.session_state.uploader_key}"
)

# -----------------------------
# Always Visible Clear Button
# -----------------------------
clear_col1, clear_col2 = st.columns([5, 1])

with clear_col2:
    if st.button("🧹 Clear All", use_container_width=True):
        st.session_state.trigger_reset = True
        st.rerun()

# Compute fingerprint to track changes in uploaded files
current_fingerprint = None
if uploaded_files:
    current_fingerprint = "+".join([f"{f.name}_{f.size}" for f in uploaded_files])

# Reset processing state only when the uploaded files change
if current_fingerprint != st.session_state.last_uploaded_fingerprint:
    st.session_state.processed = False
    st.session_state.processing_triggered = False
    st.session_state.main_pdf_data = None
    st.session_state.duplicate_pdf_data = None
    st.session_state.cropped_pdf_data = None
    st.session_state.all_pages = []
    st.session_state.main_pages = []
    st.session_state.duplicate_pages = []
    st.session_state.exchange_orders = []
    st.session_state.bulk_orders = []
    st.session_state.duplicate_groups = []

    st.session_state.last_uploaded_fingerprint = current_fingerprint

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


def get_free_size_color(sku):
    sku = sku.upper()

    if any(x in sku for x in ["BLUE", "BLU", "NAVY", "NVY"]):
        return "BLUE"
    elif any(x in sku for x in ["BLACK", "BLK"]):
        return "BLACK"
    elif any(x in sku for x in ["WHITE", "WHT"]):
        return "WHITE"
    elif any(x in sku for x in ["MAROON", "MRN"]):
        return "MAROON"
    elif any(x in sku for x in ["MULTI", "MULTICOLOUR", "MULTICOLOR", "MIX"]):
        return "MULTICOLOUR"

    return "UNKNOWN"


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

            # PART 1 - Replaced with Dynamic Free Size color token processing block
            if not size:

                if "FREE SIZE" in product.upper():

                    size = "FREE SIZE"

                    m = re.search(r"FREE SIZE\s+(\d+)", product, re.I)
                    if m:
                        qty = int(m.group(1))

                    #
                    # Detect colour dynamically
                    #

                    color = ""

                    upper_tokens = [t.upper() for t in tokens]

                    try:

                        fs_index = upper_tokens.index("FREE")

                        # FREE SIZE
                        if upper_tokens[fs_index + 1] == "SIZE":

                            start = fs_index + 3       # after FREE SIZE Qty

                            colour_tokens = []

                            for token in tokens[start:]:

                                if token == tokens[-1]:
                                    break

                                colour_tokens.append(token)

                            color = " ".join(colour_tokens).strip()

                    except:
                        color = ""

                    if not color:
                        color = "NA"

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
    st.subheader("📊 Sorting Summary")
    
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Main PDF", len(main_pages))
    c2.metric("Duplicate PDF", len(duplicate_pages))
    c3.metric("Exchange", len(exchange_orders))
    c4.metric("Bulk Qty", len(bulk_orders))
    
    normal_orders = len(main_pages) - len(exchange_orders) - len(bulk_orders)
    c5.metric("New Orders", normal_orders)

    normal_start = 1
    normal_end = normal_orders

    exchange_start = normal_end + 1
    exchange_end = exchange_start + len(exchange_orders) - 1

    bulk_start = exchange_end + 1
    bulk_end = bulk_start + len(bulk_orders) - 1

    page_summary = [
        {
            "Section": "New Orders",
            "Pages": f"{normal_start}-{normal_end}"
        }
    ]

    if exchange_orders:
        page_summary.append(
            {
                "Section": "Exchange",
                "Pages": f"{exchange_start}-{exchange_end}"
            }
        )

    if bulk_orders:
        page_summary.append(
            {
                "Section": "Bulk Qty",
                "Pages": f"{bulk_start}-{bulk_end}"
            }
        )

    st.markdown("### 📑 Page Ranges")
    st.table(page_summary)

    rows = []
    for new_page, page in enumerate(main_pages, start=1):
        if page["is_exchange"]:
            bucket = "Exchange"
            page_type = "Exchange"
        elif page["qty"] > 1:
            bucket = "Bulk Qty"
            page_type = "Bulk"
        else:
            bucket = "Normal"
            page_type = "New Order"

        rows.append(
            {
                "Output": new_page,
                "Original": page["page"],
                "Customer": page["name"],
                "SKU": page["sku"],
                "Color": page["color"],
                "Size": page["size"],
                "Qty": page["qty"],
                "Section": page_type,
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
                "SKU": page["sku"],
                "Color": page["color"],
                "Size": page["size"],
                "Qty": page["qty"],
                "Section": "Duplicate",
                "Bucket": "Duplicate",
            }
        )

    with st.expander("📄 Page Movement & Ranges"):
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
        writer.add_page(reader.pages[page["idx"]])
        progress.progress((i + 1) / total)
    progress.empty()
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer.getvalue()


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
    return pdf


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


# UPDATED: Completely dynamic tracking matrices for FREE SIZE fields
def show_packing_summary(all_pages):

    normal_summary = defaultdict(int)
    free_size_summary = defaultdict(int)
    unknown_skus = defaultdict(int)

    grand_total = 0

    for page in all_pages:

        qty = page["qty"]
        size = page["size"].upper().strip()
        color = page["color"].upper().strip() if page["color"] else ""
        sku = page["sku"]

        grand_total += qty

        # FREE SIZE PRODUCTS
        if size == "FREE SIZE":

            # PART 2 - Dynamically track colors directly derived from label parsing routing
            colour = page["color"].strip()

            if colour.upper() == "NA":

                free_size_summary["NA"] += qty
                unknown_skus[sku] += qty

            else:

                free_size_summary[colour.title()] += qty

        else:

            normal_summary[(color, size)] += qty

    st.markdown("---")
    st.subheader("📦 Packing Summary Matrix (Main PDF)")

    colors = [
        "NAVY BLUE",
        "BLACK"
    ]

    sizes = [
        "S",
        "M",
        "L",
        "XL",
        "XXL",
        "XXXL"
    ]

    # --------------------
    # NAVY & BLACK
    # --------------------

    for color in colors:

        st.markdown(f"### {color}")

        rows = []

        subtotal = 0

        for size in sizes:

            qty = normal_summary[(color, size)]

            subtotal += qty

            rows.append(
                {
                    "Size": size,
                    "Qty": qty
                }
            )

        st.table(rows)

        st.success(f"Total {color} : {subtotal}")

    # --------------------
    # FREE SIZE
    # --------------------

    st.markdown("### FREE SIZE")

    rows = []

    subtotal = 0

    # PART 3 - Loop over unique sorted dynamic color tracking keys, isolating NA to the absolute bottom
    for colour in sorted(free_size_summary.keys()):

        if colour == "NA":
            continue

        qty = free_size_summary[colour]

        subtotal += qty

        rows.append(
            {
                "Colour": colour,
                "Qty": qty
            }
        )

    #
    # NA row
    #

    if free_size_summary["NA"]:

        subtotal += free_size_summary["NA"]

        rows.append(
            {
                "Colour": "NA",
                "Qty": free_size_summary["NA"]
            }
        )

    st.table(rows)

    # PART 4 - Unknown SKU structural breakdown triggered conditionally only when an explicit NA flag exists
    if free_size_summary["NA"] > 0:

        st.markdown("#### Unknown SKU Breakdown")

        sku_rows = []

        for sku, qty in sorted(
            unknown_skus.items(),
            key=lambda x: x[0]
        ):
            sku_rows.append(
                {
                    "SKU": sku,
                    "Qty": qty
                }
            )

        st.table(sku_rows)

    st.success(f"Total FREE SIZE : {subtotal}")

    st.info(f"Grand Total Pieces : {grand_total}")


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
        
        if (
            page["size"] == "FREE SIZE"
            and page["color"] == "NA"
        ):
            issues.append("Unknown Free Size Colour in SKU")
            
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


# --- APPLICATION FLOW ENGINE LAYER ---
if uploaded_files:
    if st.session_state.processed:
        process_triggered = False
        reprocess_triggered = False
    else:
        process_triggered = st.button(
            "🚀 Process PDF",
            use_container_width=True,
            type="primary"
        )
        reprocess_triggered = False

    if (process_triggered or reprocess_triggered) and not st.session_state.processing_triggered:
        st.session_state.processing_triggered = True
        
        with st.spinner("Merging uploaded files into unified buffer..."):
            combined_writer = pypdf.PdfWriter()
            for uploaded_file in uploaded_files:
                reader = pypdf.PdfReader(uploaded_file)
                for page in reader.pages:
                    combined_writer.add_page(page)
            
            buffer = io.BytesIO()
            combined_writer.write(buffer)
            buffer.seek(0)
            file_bytes = buffer.getvalue()

        with st.spinner("Extracting tokens & mapping logistics matrix..."):
            reader, all_pages = parse_pdf(file_bytes)

        (
            main_pages,
            duplicate_pages,
            normal_orders,
            exchange_orders,
            bulk_orders,
            duplicate_groups,
        ) = build_final_order(all_pages)

        with st.spinner("Rendering output document structures..."):
            main_pdf_bytes = generate_pdf(reader, main_pages)
            duplicate_pdf_bytes = generate_pdf(reader, duplicate_pages)
            cropped_pdf_bytes = generate_cropped_pdf(file_bytes, main_pages)

        st.session_state.all_pages = all_pages
        st.session_state.main_pages = main_pages
        st.session_state.duplicate_pages = duplicate_pages
        st.session_state.exchange_orders = exchange_orders
        st.session_state.bulk_orders = bulk_orders
        st.session_state.duplicate_groups = duplicate_groups
        
        st.session_state.main_pdf_data = main_pdf_bytes
        st.session_state.duplicate_pdf_data = duplicate_pdf_bytes
        st.session_state.cropped_pdf_data = cropped_pdf_bytes
        
        st.session_state.processed = True
        st.session_state.processing_triggered = False

    if st.session_state.processed:
        st.success(
            f"Processed {len(st.session_state.all_pages)} Pages\n\n"
            f"Main PDF : {len(st.session_state.main_pages)} Pages\n\n"
            f"Duplicate PDF : {len(st.session_state.duplicate_pages)} Pages"
        )

        show_debug_table(
            st.session_state.main_pages, 
            st.session_state.duplicate_pages, 
            st.session_state.exchange_orders, 
            st.session_state.bulk_orders
        )

        st.markdown("---")
        st.subheader("📥 Download PDFs")
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.download_button(
                "📄 Main PDF",
                data=st.session_state.main_pdf_data,
                file_name="Sorted_Main.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with col2:
            st.download_button(
                "👥 Duplicate PDF",
                data=st.session_state.duplicate_pdf_data,
                file_name="Duplicate_Orders.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with col3:
            st.download_button(
                "✂️ Cropped PDF",
                data=st.session_state.cropped_pdf_data,
                file_name="Cropped_Main.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        
        st.markdown("---")
        show_exchange_summary(st.session_state.exchange_orders)
        show_packing_summary(st.session_state.main_pages)
        show_parser_warnings(st.session_state.all_pages)

        st.markdown("---")
        if st.session_state.duplicate_pages:
            show_duplicate_groups(st.session_state.duplicate_groups)
        st.markdown("---")
        st.success("Done ✅")
else:
    st.warning("Awaiting file upload context. Please drop label manifest files above.")
