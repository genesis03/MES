"""Freeze existing plan shift hours before replacing the Friday default."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text


def ensure_production_plan_work_times(engine, today=None):
    today=today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    with engine.begin() as connection:
        rows=connection.execute(text('''
            SELECT DISTINCT p.equipment_id, d.work_date
            FROM production_month_plan_lines p
            JOIN production_month_plan_days d ON d.line_id=p.id
            LEFT JOIN production_plan_work_times t
              ON t.equipment_id=p.equipment_id AND t.work_date=d.work_date
            WHERE t.id IS NULL
        ''')).all()
        values=[]
        for equipment_id,work_date in rows:
            day=date.fromisoformat(work_date)
            # Previously saved past shifts used Friday 21 hours; new/future defaults use 24.
            values.append(dict(equipment_id=equipment_id,work_date=work_date,
                               end_time='05:00' if day<today and day.weekday()==4 else '08:00'))
        if values:
            connection.execute(text('''INSERT INTO production_plan_work_times
                (equipment_id,work_date,start_time,end_time,next_day)
                VALUES (:equipment_id,:work_date,'08:00',:end_time,1)'''),values)
