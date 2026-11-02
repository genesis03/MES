"""Literal, case-insensitive partial lookup. Callers retain their item eligibility filters."""
from fastapi import HTTPException
from sqlalchemy import func, case
from models.models import ItemMasterModel


def matching_parts(query,term):
    return query.filter(func.lower(ItemMasterModel.part_no).contains(term.strip().lower(),autoescape=True))


def ordered_parts(query,term):
    exact=func.lower(ItemMasterModel.part_no)==term.strip().lower()
    return query.order_by(case((exact,0),else_=1),ItemMasterModel.part_no,ItemMasterModel.id)


def unique_part(query,term,*,ambiguous_error=True):
    key=term.strip()
    if not key:return None
    exact=query.filter(func.lower(ItemMasterModel.part_no)==key.lower()).limit(2).all()
    if len(exact)==1:return exact[0]
    matches=ordered_parts(matching_parts(query,key),key).limit(21).all()
    if len(matches)==1:return matches[0]
    if matches and ambiguous_error:
        names=', '.join(row.part_no for row in matches[:20])
        raise HTTPException(409,'여러 품번이 검색됩니다. 목록에서 실제 품번을 선택하세요: '+names+(' 외' if len(matches)>20 else ''))
    return None
