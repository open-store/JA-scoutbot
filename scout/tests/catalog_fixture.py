"""Fixture catalog rows for tests.

These rows imitate the shape of the catalog query in
``scout/repositories/product_catalog_repository.py``. They are test data,
not the Jack Archer catalog: style names, families, and seasons here are
chosen to exercise style, family, alias, and ambiguity handling.
"""
from scout.taxonomy.products import ProductCatalog, catalog_from_rows

FIXTURE_SOURCE = "FIXTURE.CATALOG"

CATALOG_ROWS = [
    {"STYLE_NAME": "Atlas Short", "FRANCHISE": "Atlas", "CATEGORY": "Bottoms", "CLASS": "Shorts",
     "SUBCLASS": "Chino Shorts", "SEASONS": "SP26|SU26", "LIFECYCLES": "Launch"},
    {"STYLE_NAME": "Atlas Pant", "FRANCHISE": "Atlas", "CATEGORY": "Bottoms", "CLASS": "Pants",
     "SUBCLASS": "Chinos", "SEASONS": "FA25"},
    {"STYLE_NAME": "Charter Twill Pant", "FRANCHISE": "Charter", "CATEGORY": "Bottoms", "CLASS": "Pants",
     "SUBCLASS": "Twill", "SEASONS": "FA25|SP26"},
    {"STYLE_NAME": "Charter Oxford Shirt", "FRANCHISE": "Charter", "CATEGORY": "Tops", "CLASS": "Shirts",
     "SUBCLASS": "Oxford", "SEASONS": "SP26"},
    {"STYLE_NAME": "Clubhouse Polo", "FRANCHISE": "Clubhouse", "CATEGORY": "Tops", "CLASS": "Polos",
     "SUBCLASS": "Performance", "SEASONS": "Prior|SP26"},
    {"STYLE_NAME": "Jetsetter Tech Pant", "FRANCHISE": "Jetsetter", "CATEGORY": "Bottoms", "CLASS": "Pants",
     "SUBCLASS": "Tech", "SEASONS": "Prior|FA25"},
    {"STYLE_NAME": "Jetsetter Travel Pant", "FRANCHISE": "Jetsetter", "CATEGORY": "Bottoms", "CLASS": "Pants",
     "SUBCLASS": "Travel", "SEASONS": "SP26"},
    {"STYLE_NAME": "Jetsetter Short", "FRANCHISE": "Jetsetter", "CATEGORY": "Bottoms", "CLASS": "Shorts",
     "SUBCLASS": "Tech", "SEASONS": "SU26"},
    {"STYLE_NAME": "Anytime Crewneck", "FRANCHISE": "Anytime", "CATEGORY": "Tops", "CLASS": "Knits",
     "SUBCLASS": "Crewneck", "SEASONS": "Prior"},
]


def fixture_catalog() -> ProductCatalog:
    return catalog_from_rows(CATALOG_ROWS, source=FIXTURE_SOURCE)
