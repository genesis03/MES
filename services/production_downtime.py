from datetime import datetime
from fastapi import HTTPException


def parse_time(value):
    try:
        return datetime.strptime(value.replace('T', ' '), '%Y-%m-%d %H:%M')
    except (ValueError, AttributeError):
        raise HTTPException(422, '시간을 분 단위의 올바른 날짜·시간으로 입력하세요.')


def validate_bounds(start, end, run_start, run_end):
    start, end = parse_time(start), parse_time(end)
    if end <= start:
        raise HTTPException(422, '비가동 종료시간은 시작시간보다 늦어야 합니다.')
    if start < parse_time(run_start) or (run_end and end > parse_time(run_end)):
        raise HTTPException(422, '비가동 시간은 가동 시작·종료시간 안에 있어야 합니다.')
    if not run_end and end > datetime.now():
        raise HTTPException(422, '진행 중인 가동 건에는 미래의 비가동 시간을 등록할 수 없습니다.')
    return start, end


def serialize_downtime(row):
    return dict(id=row.id,type_code=row.type_code,type_name=row.type_name,
                started_at=row.started_at,ended_at=row.ended_at,
                minutes=int((parse_time(row.ended_at)-parse_time(row.started_at)).total_seconds()/60),
                action=row.action or '',quality_confirmed=bool(row.quality_confirmed))
