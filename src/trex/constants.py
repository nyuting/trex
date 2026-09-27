"""Fixed vocabularies: expense categories, card labels, issuer markers."""

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

#: The category `trex summarize` also lists line by line, to check insurance claims against.
HEALTHCARE_CATEGORY_ID = 8

#: Card id -> card label, as recorded in the `card` column of cat.csv.
CARDS_BY_ID: dict[int, str] = {
    1: "CHASE",
    2: "PLG",
    3: "PLY",
    4: "UOB-AMEX",
    5: "UOB-VISA",
    6: "UOB-ONE",
    7: "UOB-LADY",
}

#: The Chase card, which categorization and summaries treat specially.
CHASE_CARD_ID = 1
CHASE_CARD_LABEL = CARDS_BY_ID[CHASE_CARD_ID]

#: Chase statements are in USD; every other card is already SGD.
USD_CARD = CHASE_CARD_LABEL

#: Card label -> card id (inverse of CARDS_BY_ID).
CARD_IDS_BY_LABEL: dict[str, int] = {label: id_ for id_, label in CARDS_BY_ID.items()}

#: Issuer families, i.e. which statement parser handles a given file.
ISSUER_CHASE = "Chase"
ISSUER_PAYLAH = "Paylah"
ISSUER_UOB = "UOB"

#: Upper-cased filename prefix -> issuer family.
ISSUER_BY_PREFIX: dict[str, str] = {
    "CHASE": ISSUER_CHASE,
    "PLG": ISSUER_PAYLAH,
    "PLY": ISSUER_PAYLAH,
    "UOB": ISSUER_UOB,
}

#: Section header text found in a UOB statement -> the card label it introduces.
UOB_CARD_HEADERS: dict[str, str] = {
    "UOB ABSOLUTE CASHBACK AMEX": "UOB-AMEX",
    "PREFERRED PLATINUM VISA": "UOB-VISA",
    "PREFERRED VISA": "UOB-VISA",
    "UOB ONE CARD": "UOB-ONE",
    "LADY'S SOLITAIRE CARD": "UOB-LADY",
}

#: PayLah wallet top-ups are transfers, not spending, and are dropped.
PAYLAH_TOPUP_REMARK = "TOP UP WALLET FROM MY ACCOUNT"

#: Label used in place of a category id for transactions no rule matched.
UNCATEGORIZED_LABEL = "uncategorized"

#: Label for the section holding transactions that matched several categories.
MULTI_CATEGORY_LABEL = "multi-category"

#: Remarks for money sent to a person. A rule for one names that person, so it
#: belongs in cat_personal.csv, which is edited manually, and never in cat.csv.
PERSONAL_TRANSFER_PREFIXES: tuple[str, ...] = ("SEND MONEY TO ",)
