"""Add inspection identity fields without guessing classifications of old rows."""
from sqlalchemy import inspect, text


def ensure_inspection_link_columns(engine):
    with engine.begin() as connection:
        inspector = inspect(connection)
        if 'inspection_standards' not in inspector.get_table_names():
            return
        present = {x['name'] for x in inspector.get_columns('inspection_standards')}
        for name, definition in {'inspection_category': 'VARCHAR(30)',
                                 'inspection_process_code': 'VARCHAR(50)'}.items():
            if name not in present:
                connection.execute(text(f'ALTER TABLE inspection_standards ADD COLUMN {name} {definition}'))
        if 'inspection_standard_items' in inspector.get_table_names():
            item_columns = {x['name'] for x in inspector.get_columns('inspection_standard_items')}
            if 'inspection_no' not in item_columns:
                connection.execute(text('ALTER TABLE inspection_standard_items ADD COLUMN inspection_no VARCHAR(50)'))
                connection.execute(text('UPDATE inspection_standard_items SET inspection_no = CAST(sort_order AS VARCHAR(50))'))
