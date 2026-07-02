import io
import os
import re
import gc
import tempfile
from collections import defaultdict
import fitz
import pypdf
import streamlit as st

# Initialize Session State Variables at the top
if "processed" not in st.session_state:
    st.session_state.processed = False

if "results" not in st.session_state:
    st.session_state.results = None

st.set_page_config(
    page_title="Raincoat Order Sorter",
    page_icon="📦",
    layout="centered",
)

st.title("📦 Raincoat Order Sorting Engine")

# 1. RESET BUTTON BLOCK
if st.button("🔄 Reset Engine"):
    st.cache_data.clear()
    st.cache_resource.clear()
    
    # Clean up any residual temporary files stored in session state paths
    if st.session_state.results:
        for path_key in ["main_path", "duplicate_path", "cropped_path"]:
            path = st.session_state.results.get(path_key)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass

    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

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
        "india", "district", "state", "near", "opp", 
        "opposite", "landmark", "po", "post", "ps", 
        "police", "station"
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
                    m = re.search(r"FREE SIZE\s+(\d+)", product, re.I)
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
    return {"name": name, "address": address, "pin": pin, "phone": phone, "identity": identity}

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
        r"\bexchange order\b", r"\bexchange shipment\b",
        r"\breplacement order\b", r"\breplacement shipment\b",
        r"\breplacement\b", r"\bexchange\b"
    ]

    for pattern in exchange_patterns:
        if re.search(pattern, work_text):
            return True
    return False

def parse_pdf(file_path):
    reader = pypdf.PdfReader(file_path)
    pages = []
    progress = st.progress(0)
    total = len(reader.pages)

    for idx, page in enumerate(reader.pages):
        progress.progress((idx + 1) / total)
        text = page.extract_text() or ""
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
    progress.empty()
    return pages

def same_customer(identity1, identity2):
    if identity1[0] != identity2[0] or identity1[2] != identity2[2]:
        return False
    addr1, addr2 = identity1[1], identity2[1]
    words1, words2 = set(addr1.split()), set(addr2.split())
    common = len(words1 & words2)
    smaller = min(len(words1), len(words2))
    if smaller == 0:
        return False
    return (common / smaller) >= 0.85

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

    duplicate_order_groups.sort(key=lambda g: (g[0]["name"].lower(), g[0]["page"]))
    return normal_orders, exchange_orders, bulk_orders, duplicate_order_groups

def sort_normal_orders(orders):
    return sorted(orders, key=lambda x: (x["color_rank"], x["size_rank"], x["page"]))

def flatten_duplicate_groups(groups):
    final = []
    for group in groups:
        group.sort(key=lambda x: x["page"])
        final.extend(group)
    return final

def build_final_order(all_pages):
    normal_orders, exchange_orders, bulk_orders, duplicate_groups = split_orders(all_pages)

    normal_sorted = sort_normal_orders(normal_orders)
    exchange_sorted = sort_normal_orders(exchange_orders)
    bulk_sorted = sort_normal_orders(bulk_orders)
    duplicate_sorted = flatten_duplicate_groups(duplicate_groups)

    main_pdf_pages = normal_sorted + exchange_sorted + bulk_sorted
    return main_pdf_pages, duplicate_sorted, normal_sorted, exchange_sorted, bulk_sorted, duplicate_groups

def generate_pdf_on_disk(src_path, final_pages, filename):
    reader = pypdf.PdfReader(src_path)
    writer = pypdf.PdfWriter()
    
    for page in final_pages:
        writer.add_page(reader.pages[page["idx"]])
        
    out_path = os.path.join(tempfile.gettempdir(), filename)
    with open(out_path, "wb") as f:
        writer.write(f)
        
    return out_path

def generate_cropped_pdf_on_disk(src_path, main_pages, filename):
    source = fitz.open(src_path)
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

        clip = fitz.Rect(0, 0, rect.width, keep_bottom)

        if label_width is None:
            label_width = clip.width
            label_height = clip.height

        new_page = output.new_page(width=label_width, height=label_height)
        new_page.show_pdf_page(new_page.rect, source, page.number, clip=clip)

    out_path = os.path.join(tempfile.gettempdir(), filename)
    output.save(out_path, garbage=4, deflate=True)
    output.close()
    source.close()
    return out_path

def show_debug_table(main_pages, duplicate_pages):
    st.subheader("📊 Sorting Summary")
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

    with st.expander("Page Movement Matrix"):
        st.dataframe(rows, hide_index=True, use_container_width=True)

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
    st.success(f"Total Exchange Quantity: {total_qty}")

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
        st.success(f"Total {color}: {subtotal}")
    st.info(f"Grand Total Pieces: {total_qty}")

def show_duplicate_groups(duplicate_groups):
    if not duplicate_groups:
        return
    st.subheader("Duplicate Customers")
    for group in duplicate_groups:
        first = group[0]
        pages = " , ".join(str(x["page"]) for x in group)
        st.markdown(f"**{first['name']}**\n\nPages: {pages}\n\nPIN: {first['pin']}\n\nOrders: {len(group)}")

# 2. FILE UPLOADER CONTROL BLOCK (Disappears once processed)
if not st.session_state.processed:
    uploaded_files = st.file_uploader(
        "Upload one or more PDFs",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        if st.button("Process PDFs", use_container_width=True):
            with st.spinner("Writing incoming files safely to disk sandbox..."):
                # Merge input PDFs tracking streaming buffers down directly into a local scratch file
                combined_writer = pypdf.PdfWriter()
                for uploaded_file in uploaded_files:
                    combined_writer.append(uploaded_file)
                
                temp_input_path = os.path.join(tempfile.gettempdir(), "raw_combined.pdf")
                with open(temp_input_path, "wb") as f:
                    combined_writer.write(f)
                
                del combined_writer
                gc.collect()

            with st.spinner("Extracting parameters and structural data fields..."):
                all_pages = parse_pdf(temp_input_path)

            with st.spinner("Analyzing rules for duplicate profiles, batch splits, and exchange sorting..."):
                (main_pages, duplicate_pages, normal_orders, 
                 exchange_orders, bulk_orders, duplicate_groups) = build_final_order(all_pages)

            with st.spinner("Writing optimized outputs directly to system layout storage..."):
                main_pdf_path = generate_pdf_on_disk(temp_input_path, main_pages, "Sorted_Main.pdf")
                duplicate_pdf_path = generate_pdf_on_disk(temp_input_path, duplicate_pages, "Duplicate_Orders.pdf")
                cropped_pdf_path = generate_cropped_pdf_on_disk(temp_input_path, main_pages, "Cropped_Main.pdf")

            # Store absolute system file paths to Session State elements rather than binary payloads
            st.session_state.results = {
                "main_path": main_pdf_path,
                "duplicate_path": duplicate_pdf_path,
                "cropped_path": cropped_pdf_path,
                "metrics": {
                    "all_count": len(all_pages),
                    "main_count": len(main_pages),
                    "dup_count": len(duplicate_pages),
                    "exchange_count": len(exchange_orders),
                    "bulk_count": len(bulk_orders)
                },
                "data_structures": {
                    "main_pages": main_pages,
                    "duplicate_pages": duplicate_pages,
                    "exchange_orders": exchange_orders,
                    "duplicate_groups": duplicate_groups
                }
            }
            
            # Remove base input tempfile immediately from memory mapping
            try:
                os.remove(temp_input_path)
            except:
                pass
                
            st.session_state.processed = True
            gc.collect()
            st.rerun()

# 3. COMPLETED VIEW (Persistent actions without rendering loops)
else:
    metrics = st.session_state.results["metrics"]
    ds = st.session_state.results["data_structures"]

    st.success(
        f"Processed {metrics['all_count']} Pages\n\n"
        f"Main PDF: {metrics['main_count']} Pages\n\n"
        f"Duplicate PDF: {metrics['dup_count']} Pages"
    )

    st.subheader("📥 Download Generated Packages")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        with open(st.session_state.results["main_path"], "rb") as f:
            st.download_button(
                "📄 Main PDF",
                data=f,
                file_name="Sorted_Main.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
            
    with col2:
        with open(st.session_state.results["duplicate_path"], "rb") as f:
            st.download_button(
                "👥 Duplicate PDF",
                data=f,
                file_name="Duplicate_Orders.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
            
    with col3:
        with open(st.session_state.results["cropped_path"], "rb") as f:
            st.download_button(
                "✂️ Cropped PDF",
                data=f,
                file_name="Cropped_Main.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

    st.markdown("---")
    show_debug_table(ds["main_pages"], ds["duplicate_pages"])
    show_exchange_summary(ds["exchange_orders"])
    show_packing_summary(ds["main_pages"])
    
    if metrics["dup_count"] > 0:
        st.markdown("---")
        show_duplicate_groups(ds["duplicate_groups"])
        
    st.markdown("---")
    st.success("All pipelines synchronized ✅ Ready for processing adjustments.")
