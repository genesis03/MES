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
