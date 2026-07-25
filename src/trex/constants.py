"""Fixed vocabularies: expense categories, card sources, issuer markers."""

from __future__ import annotations

#: Category id -> display name. Ids are what appear in cat.csv and parsed CSVs.
CATEGORIES: dict[int, str] = {
    1: "DINING",
    2: "GROCERIES",
    3: "TRANSPORT",
    4: "KIDS",
    5: "GANSHUN",
    6: "YUTING",
    7: "HOME",
    8: "HEALTHCARE",
    9: "HOLIDAY",
    10: "GIFTS",
}

#: Source id -> card label, as recorded in the `source` column of cat.csv.
SOURCES_BY_ID: dict[int, str] = {
    1: "CHASE",
    2: "PLG",
    3: "PLY",
    4: "UOB-AMEX",
    5: "UOB-VISA",
    6: "UOB-ONE",
    7: "UOB-LADY",
}

#: Card label -> source id (inverse of SOURCES_BY_ID).
SOURCE_IDS_BY_LABEL: dict[str, int] = {label: id_ for id_, label in SOURCES_BY_ID.items()}

#: Issuer families, i.e. which statement parser handles a given file.
ISSUER_CHASE = "Chase"
ISSUER_PAYLAH = "Paylah"
ISSUER_UOB = "UOB"

#: Section header text found in a UOB statement -> the card label it introduces.
UOB_CARD_HEADERS: dict[str, str] = {
    "UOB ABSOLUTE CASHBACK AMEX": "UOB-AMEX",
    "PREFERRED PLATINUM VISA": "UOB-VISA",
    "UOB ONE CARD": "UOB-ONE",
    "LADY'S SOLITAIRE CARD": "UOB-LADY",
}

#: PayLah wallet top-ups are transfers, not spending, and are dropped.
PAYLAH_TOPUP_REMARK = "TOP UP WALLET FROM MY ACCOUNT"

#: Label used in place of a category id for transactions no rule matched.
UNCATEGORIZED_LABEL = "uncategorized"

#: Label for the section holding transactions that matched several categories.
MULTI_CATEGORY_LABEL = "multi-category"
