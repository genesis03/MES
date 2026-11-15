"""Link fixed report vendor rows to receipt identities, without fuzzy name guesses."""
import re
import unicodedata
from collections import defaultdict
from sqlalchemy import or_
from models.partner import Partner
from models.inbound_sync import ExternalInboundRecord
from models.subcontract_inbound import SubcontractInboundMaster,SubcontractInboundItem

VENDORS=('메인텍','혜성금속')


def vendor_name(value):
    text=unicodedata.normalize('NFKC',str(value or '')).casefold()
    text=re.sub(r'\(\s*[주유]\s*\)|주식회사|유한회사','',text)
    return re.sub(r'[^a-z0-9가-힣]','',text)


class VendorLinks:
    def __init__(self,db,rows,start,end,source):
        self.by_id={partner.id:partner.partner_name for partner in db.query(Partner)}
        self.by_document=defaultdict(set);self.by_item=defaultdict(set)
        self.receipts=[]
        if source=='EXTERNAL':
            numbers={row.get('inbound_no') for row in rows if row.get('inbound_no')}
            query=db.query(ExternalInboundRecord).filter(ExternalInboundRecord.present.is_(True))
            query=query.filter(or_(ExternalInboundRecord.inbound_date.between(start,end),ExternalInboundRecord.inbound_no.in_(numbers)))
            self.receipts=query.all()
            for receipt in self.receipts:
                self.by_document[receipt.inbound_no].add(receipt.partner_name)
                self.by_item[(receipt.inbound_no,receipt.part_no.strip().casefold())].add(receipt.partner_name)

    def one_group(self,names,strict=False):
        normalized={vendor_name(name) for name in names if vendor_name(name)}
        matches={group for group in VENDORS if vendor_name(group) in normalized}
        if strict and normalized-{vendor_name(group) for group in matches}:return None
        return next(iter(matches)) if len(matches)==1 else None

    def resolve(self,row,partner_field=''):
        # Native receipts have a stable partner ID even if the stored display name changed.
        name=self.by_id.get(row.get('partner_id'))
        group=self.one_group([name],strict=True) if name else None
        if group:return group
        number=row.get('inbound_no')
        for part in (row.get('source_part_no'),row.get('part_no')):
            candidates=self.by_item.get((number,str(part or '').strip().casefold()),set())
            if candidates:
                group=self.one_group(candidates,strict=True)
                return group
        candidates=self.by_document.get(number,set())
        if candidates:
            group=self.one_group(candidates,strict=True)
            return group
        raw=row.get('raw') or {}
        if partner_field in ('APPLY_COMP_NM','ORDER_COMP_NM'):
            return self.one_group([raw.get(partner_field)],strict=True)
        return self.one_group([row.get('partner_name'),raw.get('APPLY_COMP_NM'),raw.get('ORDER_COMP_NM')])

    def add_receipt_quantities(self,db,months,start,end,source):
        covered=set()
        if source=='EXTERNAL':
            for receipt in self.receipts:
                group=self.one_group([receipt.partner_name],strict=True)
                if not group:continue
                covered.add((group,receipt.inbound_no))
                if start<=receipt.inbound_date<=end:
                    months[int(receipt.inbound_date[5:7])-1]['subcontract'][group]['qty']+=float(receipt.confirmed_qty)
        else:
            receipts=(db.query(SubcontractInboundMaster,SubcontractInboundItem)
                .join(SubcontractInboundItem,SubcontractInboundItem.inbound_id==SubcontractInboundMaster.id)
                .filter(SubcontractInboundMaster.status=='RECEIVED',SubcontractInboundMaster.inbound_date.between(start,end)))
            for master,line in receipts:
                group=self.resolve({'partner_id':master.partner_id,'partner_name':master.partner_name})
                if not group:continue
                covered.add((group,master.inbound_no))
                # Legacy good_qty stores total received quantity, not received minus defects.
                months[int(master.inbound_date[5:7])-1]['subcontract'][group]['qty']+=float(line.good_qty or 0)
        return covered
