"""Product-name -> SKU matching shared by the sales ETL and the PO upload.

Accurate item codes are hierarchical: a code that has codes under it (299.052.02.011 "Tang" with
299.052.02.011.001 ...) is a group / category, never a sellable item. Group codes are kept out of
every name lookup, and names match only exactly (ignoring capitals and repeated spaces). The old
substring fallback sent any unknown name containing "tang" (sarung tangan, tangki, rectangle ...)
to the category "Tang", so it is gone: an unknown name stays unmatched and is reported instead."""


def norm_name(name) -> str:
    """Lowercase, trim and collapse inner whitespace ("Tang Set  bisa" == "tang set bisa")."""
    return " ".join(str(name).lower().split())


def group_skus(skus) -> set:
    """Codes that have at least one other code under them ("A.B" is a group when "A.B.C" exists)."""
    skus = {str(s).strip() for s in skus if s is not None and str(s).strip()}
    prefixes = set()
    for s in skus:
        parts = s.split(".")
        for i in range(1, len(parts)):
            prefixes.add(".".join(parts[:i]))
    return skus & prefixes
