"""Freeze existing plan shift hours before replacing the Friday default."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text,inspect


def ensure_production_plan_work_times(engine, today=None):
    today=today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    with engine.begin() as connection:
        columns={c['name'] for c in inspect(connection).get_columns('production_plan_work_times')}
        if 'excluded_minutes' not in columns:
            connection.execute(text('ALTER TABLE production_plan_work_times ADD COLUMN excluded_minutes INTEGER NOT NULL DEFAULT 0'))
            # One-time correction of old tapping 24-hour defaults; keep shorter custom shifts.
            connection.execute(text("""UPDATE production_plan_work_times SET excluded_minutes=210
                WHERE next_day=1 AND start_time=end_time AND equipment_id IN
                (SELECT id FROM equipment_masters WHERE process_code='TP')"""))
        # Correct the superseded 190-minute default once; subsequent user edits are preserved.
        connection.execute(text('CREATE TABLE IF NOT EXISTS production_plan_time_migrations (code VARCHAR(80) PRIMARY KEY)'))
        correction='tapping-default-210-20261013'
        if not connection.execute(text('SELECT code FROM production_plan_time_migrations WHERE code=:code'),{'code':correction}).first():
            connection.execute(text("""UPDATE production_plan_work_times SET excluded_minutes=210
                WHERE excluded_minutes=190 AND next_day=1 AND start_time=end_time AND equipment_id IN
                (SELECT id FROM equipment_masters WHERE process_code='TP')"""))
            connection.execute(text('INSERT INTO production_plan_time_migrations (code) VALUES (:code)'),{'code':correction})
        rows=connection.execute(text('''
            SELECT DISTINCT p.equipment_id, d.work_date, e.process_code
            FROM production_month_plan_lines p
            JOIN production_month_plan_days d ON d.line_id=p.id
            JOIN equipment_masters e ON e.id=p.equipment_id
            LEFT JOIN production_plan_work_times t
              ON t.equipment_id=p.equipment_id AND t.work_date=d.work_date
            WHERE t.id IS NULL
        ''')).all()
        values=[]
        for equipment_id,work_date,process_code in rows:
            day=date.fromisoformat(work_date)
            # Previously saved past shifts used Friday 21 hours; new/future defaults use 24.
            values.append(dict(equipment_id=equipment_id,work_date=work_date,
                               end_time='05:00' if day<today and day.weekday()==4 else '08:00',excluded_minutes=210 if process_code=='TP' else 0))
        if values:
            connection.execute(text('''INSERT INTO production_plan_work_times
                (equipment_id,work_date,start_time,end_time,next_day,excluded_minutes)
                VALUES (:equipment_id,:work_date,'08:00',:end_time,1,:excluded_minutes)'''),values)
