# trex

Personal pipeline for turning PDF bank statements into categorized
monthly expense summaries.

## Flow

1. Drop PDF statement into `statements/`.
2. `pdf2csv.py` — extract tables via `tabula` into `statements_tabula/`.
3. `stmt.py` — parse tabula CSVs into clean transaction data under `statements_parsed/`,
   with categories and sources.
4. `cat.py` — auto-categorize transactions using `categories/brands.csv` and
   `categories/cat.csv`.
5. `trex.py` — consolidate parsed statements into a category × month summary
   (USD converted to SGD at `EXCHANGE_RATE_USD2SGD`).

## Categories

Defined in `stmt.py`: DINING, GROCERIES, TRANSPORT, KIDS, GANSHUN, YUTING,
HOME, HEALTHCARE, HOLIDAY, GIFTS.

## Sources

CHASE, PAYLAH, UOB-AMEX, UOB-VISA, UOB-ONE, UOB-LADY.

## Refs

Tabula: https://github.com/tabulapdf/tabula