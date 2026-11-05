"""CSV by default; XLSX support imports its optional dependency only on demand."""
import csv
from decimal import Decimal
import io


def activate(ctx, config):
    limit = Decimal(str(config.get('per_item_limit', '100')))
    def summarize(args):
        if 'xlsx' in args:
            from openpyxl import load_workbook
            book = load_workbook(args['xlsx'], read_only=True, data_only=True)
            try:
                values = list(book.active.values)
                rows = [dict(zip(values[0], row)) for row in values[1:]]
            finally:
                book.close()
        else:
            rows = list(csv.DictReader(io.StringIO(args['csv'])))
        total = Decimal(0)
        rejected = 0
        for row in rows:
            amount = Decimal(str(row['amount']))
            if not amount.is_finite() or amount < 0 or amount > limit or row.get('category') == 'alcohol':
                rejected += 1
            else:
                total += amount
        return {'total': str(total.quantize(Decimal('0.01'))), 'rejected': rejected}
    ctx.register_tool('summarize_expenses', 'Apply per-item reimbursement rules to a table', {
        'type': 'object', 'properties': {'csv': {'type': 'string'}, 'xlsx': {'type': 'string'}},
        'oneOf': [{'required': ['csv']}, {'required': ['xlsx']}], 'additionalProperties': False}, summarize)
