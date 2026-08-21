"""ETF analytics derived from the sole ARM market.db.

The owner pipeline populates instruments, daily bars and corporate actions.
This package only derives regime tags and fair-score history back into the same
database; it never creates or refreshes another market store.
"""
