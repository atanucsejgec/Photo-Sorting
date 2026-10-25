"""Label text shown as written."""


def literal(text: str) -> str:
    """text for a button (any QAbstractButton) or a QLabel with a buddy, with
    each & shown as itself rather than read as a keyboard mnemonic (which
    shows "Notes & Pages" as "Notes _Pages", and makes Alt+P press it)."""
    return text.replace("&", "&&")
