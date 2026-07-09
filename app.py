import os
import re
import shutil
import subprocess
import tempfile
from collections import OrderedDict, defaultdict
from functools import lru_cache

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
if "main_pdf_path" not in st.session_state:
    st.session_state.main_pdf_path = None
if "duplicate_pdf_path" not in st.session_state:
    st.session_state.duplicate_pdf_path = None
if "cropped_pdf_path" not in st.session_state:
    st.session_state.cropped_pdf_path = None
if "job_dir" not in st.session_state:
    st.session_state.job_dir = None
if "source_paths" not in st.session_state:
    st.session_state.source_paths = []
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
if "create_cropped_pdf" not in st.session_state:
    st.session_state.create_cropped_pdf = True
_SAVE_DIALOG_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.SaveFileDialog
$dialog.Title = "Download PDF"
$dialog.Filter = "PDF files (*.pdf)|*.pdf"
$dialog.DefaultExt = "pdf"
$dialog.AddExtension = $true
$dialog.OverwritePrompt = $true
$dialog.FileName = $env:RAINCOAT_DEFAULT_NAME

if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [System.IO.File]::Copy($env:RAINCOAT_SOURCE_PDF, $dialog.FileName, $true)
    [Console]::Out.Write($dialog.FileName)
}
"""


def save_pdf_dialog(pdf_path, default_name):
    """Open an independent native Windows Save As dialog for one PDF."""
    if not pdf_path or not os.path.isfile(pdf_path):
        st.error("This PDF is empty and cannot be saved.")
        return

    try:
        env = os.environ.copy()
        env["RAINCOAT_DEFAULT_NAME"] = default_name
        env["RAINCOAT_SOURCE_PDF"] = os.path.abspath(pdf_path)

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-Command",
                _SAVE_DIALOG_PS,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if result.returncode != 0:
            detail = result.stderr.strip()
            st.error(detail or "Windows could not open the Save As dialog.")
        # A successful save or cancellation intentionally shows no app message.
    except OSError as exc:
        st.error(f"Could not save the PDF: {exc}")


def cleanup_job_dir():
    """Remove only this session's private temporary working directory."""
    job_dir = st.session_state.get("job_dir")
    if not job_dir:
        return

    resolved = os.path.abspath(job_dir)
    temp_root = os.path.abspath(tempfile.gettempdir())
    safe_name = os.path.basename(resolved).startswith("raincoat_sorter_")

    if safe_name and os.path.commonpath([resolved, temp_root]) == temp_root:
        shutil.rmtree(resolved, ignore_errors=True)

    st.session_state.job_dir = None
    st.session_state.source_paths = []


def clear_generated_state():
    st.session_state.processed = False
    st.session_state.processing_triggered = False
    st.session_state.main_pdf_path = None
    st.session_state.duplicate_pdf_path = None
    st.session_state.cropped_pdf_path = None
    st.session_state.all_pages = []
    st.session_state.main_pages = []
    st.session_state.duplicate_pages = []
    st.session_state.exchange_orders = []
    st.session_state.bulk_orders = []
    st.session_state.duplicate_groups = []


# Reset utility to completely clear state data and file uploader
def reset_application_state():
    cleanup_job_dir()
    clear_generated_state()
    st.session_state.last_uploaded_fingerprint = None
    st.session_state.create_cropped_pdf = True

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
    key=f"pdf_uploader_file_input_{st.session_state.uploader_key}",
)

# -----------------------------
# Always Visible Clear Button
# -----------------------------
control_spacer, cropped_option_col, clear_col = st.columns([3, 2, 1])

with cropped_option_col:
    st.checkbox(
        "Create cropped PDF",
        key="create_cropped_pdf",
        disabled=st.session_state.processed,
    )

with clear_col:
    if st.button("🧹 Clear All", use_container_width=True):
        st.session_state.trigger_reset = True
        st.rerun()

# Compute fingerprint to track changes in uploaded files
current_fingerprint = None
if uploaded_files:
    current_fingerprint = "+".join(
        [
            f"{getattr(f, 'file_id', '')}_{f.name}_{f.size}"
            for f in uploaded_files
        ]
    )

# Reset processing state only when the uploaded files change
if current_fingerprint != st.session_state.last_uploaded_fingerprint:
    cleanup_job_dir()
    clear_generated_state()
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

FREE_SIZE_COLOR_RANK = {
    "BLUE": 1,
    "BLACK": 2,
    "WHITE": 3,
    "MAROON": 4,
    "MULTICOLOUR": 5,
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
    elif any(x in sku for x in ["GREY", "GRAY", "GRY"]):
        return "GREY"
    elif any(x in sku for x in ["MULTI", "MULTICOLOUR", "MULTICOLOR", "MIX"]):
        return "MULTICOLOUR"

    return "UNKNOWN"


def get_free_size_sort_color(color, sku):
    """Return a canonical colour name used only for Free Size sorting."""
    normalized_color = normalize(color).upper()

    if normalized_color and normalized_color not in {"NA", "UNKNOWN"}:
        if any(
            value in normalized_color
            for value in ("BLUE", "BLU", "NAVY", "NVY")
        ):
            return "BLUE"
        if "BLACK" in normalized_color or "BLK" in normalized_color:
            return "BLACK"
        if "WHITE" in normalized_color or "WHT" in normalized_color:
            return "WHITE"
        if "MAROON" in normalized_color or "MRN" in normalized_color:
            return "MAROON"
        if any(
            value in normalized_color
            for value in ("GREY", "GRAY", "GRY")
        ):
            return "GREY"
        if any(
            value in normalized_color
            for value in ("MULTI", "MULTICOLOUR", "MULTICOLOR", "MIX")
        ):
            return "MULTICOLOUR"
        return normalized_color

    return get_free_size_color(sku)


def order_sort_key(order):
    """Preserve normal sorting and group Free Size products by colour."""
    if order["size"].upper().strip() == "FREE SIZE":
        free_color = get_free_size_sort_color(
            order["color"], order["sku"]
        )
        free_color_rank = FREE_SIZE_COLOR_RANK.get(free_color, 98)
        return (
            3,
            0,
            free_color_rank,
            free_color,
            order["page"],
        )

    return (
        order["color_rank"],
        order["size_rank"],
        0,
        "",
        order["page"],
    )


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

                    color = ""
                    upper_tokens = [t.upper() for t in tokens]

                    try:
                        fs_index = upper_tokens.index("FREE")
                        if upper_tokens[fs_index + 1] == "SIZE":
                            start = fs_index + 3
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

    customer_block = re.search(
        r"Customer Address[\s\S]*?If undelivered", work_text, re.I
    )
    if customer_block:
        work_text = work_text.replace(customer_block.group(0), "")

    bill_block = re.search(r"BILL TO\s*/\s*SHIP TO[\s\S]*", work_text, re.I)
    if bill_block:
        work_text = work_text[: bill_block.start()]

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


def prepare_input_files(uploaded_files, job_dir):
    """Copy uploads to disk without creating a second combined PDF in RAM."""
    total_size = sum(uploaded_file.size for uploaded_file in uploaded_files)
    free_space = shutil.disk_usage(job_dir).free
    required_space = (total_size * 4) + (100 * 1024 * 1024)

    if free_space < required_space:
        required_gb = required_space / (1024**3)
        free_gb = free_space / (1024**3)
        raise RuntimeError(
            f"Not enough temporary disk space. Need about {required_gb:.1f} GB; "
            f"only {free_gb:.1f} GB is available."
        )

    source_paths = []
    for file_number, uploaded_file in enumerate(uploaded_files, start=1):
        safe_name = os.path.basename(uploaded_file.name) or "uploaded.pdf"
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", safe_name)
        safe_name = safe_name[-120:]
        source_path = os.path.join(
            job_dir, f"input_{file_number:04d}_{safe_name}"
        )
        uploaded_file.seek(0)
        with open(source_path, "wb") as destination:
            shutil.copyfileobj(uploaded_file, destination, length=8 * 1024 * 1024)
        source_paths.append(source_path)

    return source_paths


def parse_pdf_sources(source_paths):
    """Parse one or many PDFs while retaining only compact page metadata."""
    pages = []
    progress = st.progress(0)
    total = 0

    for source_path in source_paths:
        with fitz.open(source_path) as document:
            total += document.page_count

    if total == 0:
        progress.empty()
        raise RuntimeError("The uploaded PDF contains no pages.")

    update_every = max(1, total // 200)
    completed = 0

    try:
        for source_path in source_paths:
            with open(source_path, "rb") as source_file:
                reader = pypdf.PdfReader(source_file, strict=False)

                if reader.is_encrypted:
                    try:
                        decrypt_result = reader.decrypt("")
                        if not decrypt_result:
                            raise ValueError("A password is required")
                    except Exception as exc:
                        raise RuntimeError(
                            f"Password-protected PDF cannot be processed: "
                            f"{os.path.basename(source_path)}"
                        ) from exc

                for source_page, pdf_page in enumerate(reader.pages):
                    parse_error = ""
                    try:
                        text = pdf_page.extract_text() or ""
                    except Exception as exc:
                        text = ""
                        parse_error = type(exc).__name__

                    product = parse_product_table(text)
                    customer = extract_customer(text)
                    is_exchange = detect_exchange(text)

                    pages.append(
                        {
                            "idx": completed,
                            "source_path": source_path,
                            "source_page": source_page,
                            "page": completed + 1,
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
                            "parse_error": parse_error,
                        }
                    )

                    completed += 1
                    if completed % update_every == 0 or completed == total:
                        progress.progress(completed / total)
    finally:
        progress.empty()

    return pages


def consolidate_many_sources(source_paths, all_pages, job_dir):
    """Avoid repeatedly reopening files when a large batch is interleaved."""
    if len(source_paths) <= 8:
        return

    merged_path = os.path.join(job_dir, "_optimized_source.pdf")
    merged = fitz.open()

    try:
        for source_path in source_paths:
            with fitz.open(source_path) as source:
                merged.insert_pdf(
                    source,
                    links=True,
                    annots=True,
                )
        merged.save(merged_path, garbage=0, deflate=False)
    finally:
        merged.close()

    for page in all_pages:
        page["source_path"] = merged_path
        page["source_page"] = page["idx"]


@lru_cache(maxsize=50000)
def address_words(address):
    return frozenset(address.split())


def same_customer(identity1, identity2):
    if identity1[0] != identity2[0]:
        return False
    if identity1[2] != identity2[2]:
        return False

    addr1 = identity1[1]
    addr2 = identity2[1]

    words1 = address_words(addr1)
    words2 = address_words(addr2)

    common = len(words1 & words2)
    smaller = min(len(words1), len(words2))

    if smaller == 0:
        return False

    similarity = common / smaller
    return similarity >= 0.85


def split_orders(all_pages):
    groups = []
    candidate_groups = defaultdict(list)

    for page in all_pages:
        found = False
        identity = page["identity"]
        bucket_key = (identity[0], identity[2])

        for group_index in candidate_groups[bucket_key]:
            group = groups[group_index]
            if same_customer(page["identity"], group[0]["identity"]):
                group.append(page)
                found = True
                break

        if not found:
            group_index = len(groups)
            groups.append([page])
            candidate_groups[bucket_key].append(group_index)

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
    return sorted(orders, key=order_sort_key)


def sort_bulk_orders(orders):
    return sorted(orders, key=order_sort_key)


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

    page_summary = [{"Section": "New Orders", "Pages": f"{normal_start}-{normal_end}"}]

    if exchange_orders:
        page_summary.append(
            {"Section": "Exchange", "Pages": f"{exchange_start}-{exchange_end}"}
        )

    if bulk_orders:
        page_summary.append(
            {"Section": "Bulk Qty", "Pages": f"{bulk_start}-{bulk_end}"}
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


def get_source_document(document_cache, source_path, max_open=8):
    """Return a source PDF while limiting simultaneously open file handles."""
    if source_path in document_cache:
        document = document_cache.pop(source_path)
        document_cache[source_path] = document
        return document

    document = fitz.open(source_path)
    document_cache[source_path] = document

    while len(document_cache) > max_open:
        _, old_document = document_cache.popitem(last=False)
        old_document.close()

    return document


def close_source_documents(document_cache):
    for document in document_cache.values():
        document.close()
    document_cache.clear()


def write_empty_pdf(output_path):
    writer = pypdf.PdfWriter()
    with open(output_path, "wb") as output_file:
        writer.write(output_file)


def generate_pdf(final_pages, output_path):
    """Create a reordered PDF directly on disk, with bounded Python memory."""
    if not final_pages:
        write_empty_pdf(output_path)
        return output_path

    output = fitz.open()
    document_cache = OrderedDict()
    progress = st.progress(0)
    total = len(final_pages)
    update_every = max(1, total // 200)

    try:
        position = 0
        while position < total:
            first = final_pages[position]
            source_path = first["source_path"]
            first_page = first["source_page"]
            last_page = first_page
            run_end = position + 1

            while run_end < total:
                candidate = final_pages[run_end]
                if (
                    candidate["source_path"] != source_path
                    or candidate["source_page"] != last_page + 1
                ):
                    break
                last_page = candidate["source_page"]
                run_end += 1

            source = get_source_document(document_cache, source_path)
            output.insert_pdf(
                source,
                from_page=first_page,
                to_page=last_page,
                links=True,
                annots=True,
            )

            if run_end % update_every == 0 or run_end == total:
                progress.progress(run_end / total)
            position = run_end

        output.save(output_path, garbage=0, deflate=False)
    finally:
        progress.empty()
        close_source_documents(document_cache)
        output.close()

    return output_path


def generate_cropped_pdf(main_pages, output_path):
    """Create the cropped PDF directly on disk."""
    if not main_pages:
        write_empty_pdf(output_path)
        return output_path

    output = fitz.open()
    document_cache = OrderedDict()
    progress = st.progress(0)
    total = len(main_pages)
    update_every = max(1, total // 200)
    label_width = None
    label_height = None

    try:
        for completed, page_info in enumerate(main_pages, start=1):
            source = get_source_document(
                document_cache, page_info["source_path"]
            )
            page = source.load_page(page_info["source_page"])
            rect = page.rect
            order_box = page.search_for("Order No.")

            if order_box:
                keep_bottom = min(order_box[0].y1 + 40, rect.height)
            else:
                keep_bottom = rect.height

            clip = fitz.Rect(0, 0, rect.width, keep_bottom)

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

            if completed % update_every == 0 or completed == total:
                progress.progress(completed / total)

        output.save(output_path, garbage=0, deflate=False)
    finally:
        progress.empty()
        close_source_documents(document_cache)
        output.close()

    return output_path


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

        if size == "FREE SIZE":
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

    colors = ["NAVY BLUE", "BLACK"]
    sizes = ["S", "M", "L", "XL", "XXL", "XXXL"]

    for color in colors:
        st.markdown(f"### {color}")
        rows = []
        subtotal = 0
        for size in sizes:
            qty = normal_summary[(color, size)]
            subtotal += qty
            rows.append({"Size": size, "Qty": qty})
        st.table(rows)
        st.success(f"Total {color} : {subtotal}")

    st.markdown("### FREE SIZE")
    rows = []
    subtotal = 0

    for colour in sorted(free_size_summary.keys()):
        if colour == "NA":
            continue
        qty = free_size_summary[colour]
        subtotal += qty
        rows.append({"Colour": colour, "Qty": qty})

    if free_size_summary["NA"]:
        subtotal += free_size_summary["NA"]
        rows.append({"Colour": "NA", "Qty": free_size_summary["NA"]})

    st.table(rows)

    if free_size_summary["NA"] > 0:
        st.markdown("#### Unknown SKU Breakdown")
        sku_rows = []
        for sku, qty in sorted(unknown_skus.items(), key=lambda x: x[0]):
            sku_rows.append({"SKU": sku, "Qty": qty})
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

        if page["size"] == "FREE SIZE" and page["color"] == "NA":
            issues.append("Unknown Free Size Colour in SKU")

        if page["qty"] <= 0:
            issues.append(f"Invalid Quantity ({page['qty']})")

        if page.get("parse_error"):
            issues.append(f"Text extraction error ({page['parse_error']})")

        if issues:
            warnings.append(
                {
                    "Page": page["page"],
                    "Customer": page["name"],
                    "Issues Found": ", ".join(issues),
                }
            )

    if warnings:
        st.markdown("---")
        st.warning(
            "⚠️ Parser Warnings (Verify these labels manually before printing)"
        )
        st.dataframe(warnings, hide_index=True, use_container_width=True)


# --- APPLICATION FLOW ENGINE LAYER ---
if uploaded_files:
    if st.session_state.processed:
        process_triggered = False
        reprocess_triggered = False
    else:
        process_triggered = st.button(
            "🚀 Process PDF", use_container_width=True, type="primary"
        )
        reprocess_triggered = False

    if (
        process_triggered or reprocess_triggered
    ) and not st.session_state.processing_triggered:
        st.session_state.processing_triggered = True

        try:
            cleanup_job_dir()
            job_dir = tempfile.mkdtemp(prefix="raincoat_sorter_")
            st.session_state.job_dir = job_dir

            with st.spinner("Preparing uploaded PDFs on disk..."):
                source_paths = prepare_input_files(uploaded_files, job_dir)
                st.session_state.source_paths = source_paths

            with st.spinner("Extracting tokens & mapping logistics matrix..."):
                address_words.cache_clear()
                all_pages = parse_pdf_sources(source_paths)
                consolidate_many_sources(source_paths, all_pages, job_dir)

            (
                main_pages,
                duplicate_pages,
                normal_orders,
                exchange_orders,
                bulk_orders,
                duplicate_groups,
            ) = build_final_order(all_pages)

            main_pdf_path = os.path.join(job_dir, "Sorted_Main.pdf")
            duplicate_pdf_path = os.path.join(
                job_dir, "Duplicate_Orders.pdf"
            )
            create_cropped_pdf = st.session_state.create_cropped_pdf
            cropped_pdf_path = None
            if create_cropped_pdf:
                cropped_pdf_path = os.path.join(job_dir, "Cropped_Main.pdf")

            with st.spinner("Rendering main PDF..."):
                generate_pdf(main_pages, main_pdf_path)
            with st.spinner("Rendering duplicate PDF..."):
                generate_pdf(duplicate_pages, duplicate_pdf_path)
            if create_cropped_pdf:
                with st.spinner("Rendering cropped PDF..."):
                    generate_cropped_pdf(main_pages, cropped_pdf_path)

            st.session_state.all_pages = all_pages
            st.session_state.main_pages = main_pages
            st.session_state.duplicate_pages = duplicate_pages
            st.session_state.exchange_orders = exchange_orders
            st.session_state.bulk_orders = bulk_orders
            st.session_state.duplicate_groups = duplicate_groups
            st.session_state.main_pdf_path = main_pdf_path
            st.session_state.duplicate_pdf_path = duplicate_pdf_path
            st.session_state.cropped_pdf_path = cropped_pdf_path
            st.session_state.processed = True
        except Exception as exc:
            cleanup_job_dir()
            clear_generated_state()
            st.error(f"Processing failed: {exc}")
        finally:
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
            st.session_state.bulk_orders,
        )

        st.markdown("---")

        # Each button opens its own native Windows Save As dialog.
        st.subheader("📥 Download PDFs")

        c1, c2, c3 = st.columns(3)

        with c1:
            if st.button(
                "📄 Main PDF",
                key="save_main_pdf",
                use_container_width=True,
            ):
                save_pdf_dialog(
                    st.session_state.main_pdf_path, "Sorted_Main.pdf"
                )

        with c2:
            if st.button(
                "👥 Duplicate PDF",
                key="save_duplicate_pdf",
                use_container_width=True,
            ):
                save_pdf_dialog(
                    st.session_state.duplicate_pdf_path,
                    "Duplicate_Orders.pdf",
                )

        with c3:
            if st.button(
                "✂️ Cropped PDF",
                key="save_cropped_pdf",
                use_container_width=True,
                disabled=not bool(st.session_state.cropped_pdf_path),
            ):
                save_pdf_dialog(
                    st.session_state.cropped_pdf_path, "Cropped_Main.pdf"
                )

        show_exchange_summary(st.session_state.exchange_orders)
        show_packing_summary(st.session_state.main_pages)
        show_parser_warnings(st.session_state.all_pages)

        st.success("Processing Completed")
else:
    st.warning(
        "Awaiting file upload context. Please drop label manifest files above."
    )
