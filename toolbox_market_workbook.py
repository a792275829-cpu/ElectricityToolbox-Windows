"""Market template engine adapter: existing style definitions, native ZIP/XML."""
from datetime import date, datetime, time, timedelta

from openpyxl.compat.strings import safe_string
from openpyxl.styles.stylesheet import write_stylesheet
from openpyxl.utils.datetime import to_excel
from openpyxl.xml.functions import tostring

import toolbox_native_workbook as native


def save_market_workbook(workbook, original, destination, expected_cells):
    if not native.transaction_available():
        return False
    changes = {}
    for sheet_name, coordinate in expected_cells:
        cell = workbook[sheet_name][coordinate]
        value = cell.value
        if value is None:
            kind, encoded = "null", None
        elif isinstance(value, bool):
            kind, encoded = "b", value
        elif isinstance(value, (datetime, date, time, timedelta)):
            if workbook.iso_dates:
                return False  # Preserve the existing ISO-date template engine.
            kind, encoded = "n", safe_string(to_excel(value, workbook.epoch))
        elif cell.data_type == "f":
            if not isinstance(value, str):
                return False  # Array/data-table formula objects stay in their engine.
            kind, encoded = "f", value[1:]
        elif cell.data_type == "e":
            return False  # Complex error-cell templates retain their engine.
        elif isinstance(value, str):
            kind, encoded = "inlineStr", value
        else:
            kind, encoded = "n", safe_string(value)
        # Accessing style_id registers styles newly inherited from another row.
        changes.setdefault(sheet_name, {})[coordinate] = dict(kind=kind, value=encoded, style_id=cell.style_id)
    styles = tostring(write_stylesheet(workbook))
    result = native.patch_package(original, destination, {}, typed_changes=changes,
                                  replace_parts={"xl/styles.xml": styles})
    return result is not None
