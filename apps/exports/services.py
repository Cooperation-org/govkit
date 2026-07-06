"""
Import/export services — STUBS for the exports agent.

import_opening_balances: parse a CSV of opening balances into OpeningBalance rows under
an ImportBatch. export_pie: render current shares as CSV / Slicing Pie contribution
format. The exports agent implements bodies; signatures are the seam.
"""

from __future__ import annotations


def import_opening_balances(org, fileobj, created_by=None):
    """Import opening balances from a CSV file-like object. Returns the ImportBatch."""
    raise NotImplementedError("Exports agent: implement opening-balance CSV import.")


def export_pie_csv(org):
    """Export the org's current pie as CSV (generic). Returns a string or file-like."""
    raise NotImplementedError("Exports agent: implement generic CSV export.")


def export_slicing_pie(org):
    """Export contributions in Slicing Pie contribution format (supported export target)."""
    raise NotImplementedError("Exports agent: implement Slicing Pie-format export.")
