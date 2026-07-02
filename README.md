# 📦 AllureX Dispatch Manager

A Streamlit application to automatically process and sort raincoat order PDFs.

## Features

- 📄 Upload Meesho / Valmo order PDF
- 🎨 Sort by Color
  - Navy Blue
  - Black
  - Free Size
- 📏 Sort by Size
  - S
  - M
  - L
  - XL
  - XXL
  - XXXL
- 🔄 Detect Exchange Orders
- 📦 Separate Bulk Orders (Qty > 1)
- 👥 Detect Duplicate Customers
- 📑 Generate Two PDFs
  - Main Orders
  - Duplicate Orders
- 📊 Packing Summary
- 🔄 Exchange Summary
- ⚠️ Parser Warning Report

## Installation

```bash
pip install -r requirements.txt
```

Run the application:

```bash
streamlit run app.py
```

## Project Structure

```
raincoat-order-sorter/

│── app.py
│── requirements.txt
│── README.md
```

## Version

Version 1.0