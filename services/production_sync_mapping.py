"""Resolve external production items using the company's stage suffixes."""
import re

from models.models import ItemMasterModel, ProcessModel
from models.production_sync import ProductionSyncItemMap, ProductionSyncProcessMap


def stage_suffix(*names):
    for name in names:
        compact = re.sub(r'\s+', '', name or '').casefold()
        matches = {suffix for keyword, suffix in (
            ('복합선반', '-A'), ('탭핑', '-B'), ('세레이션', '-D'),
            ('은도금', '-Ag'), ('조립', '-C'), ('포장', '')) if keyword in compact}
        if len(matches) == 1:
            return matches.pop()
        if matches:
            return '?'  # Compound/ambiguous stage needs a manual selection.
    return None


class ItemConnections:
    def __init__(self, db):
        self.items = db.query(ItemMasterModel).all()
        self.by_id = {item.id: item for item in self.items}
        self.by_part = {}
        for item in self.items:
            if item.is_active == 'Y':
                self.by_part.setdefault(item.part_no.casefold(), []).append(item)
        self.manual = {(row.source_part_no, row.source_process_name): row.item_id
                       for row in db.query(ProductionSyncItemMap)}
        self.processes = {row.process_code: row.process_name for row in
                          db.query(ProcessModel).filter(ProcessModel.is_active == 'Y')}
        self.by_name = {}
        for code, name in self.processes.items():
            self.by_name.setdefault(name, []).append(code)
        self.process_maps = {row.source_name: row for row in db.query(ProductionSyncProcessMap)}

    def process_code(self, source_process):
        mapping = self.process_maps.get(source_process)
        if mapping:
            return mapping.process_code
        candidates = self.by_name.get(source_process, [])
        return candidates[0] if len(candidates) == 1 else ''

    def suffix(self, source_process):
        return stage_suffix(self.processes.get(self.process_code(source_process), ''), source_process)

    def resolve(self, source_part, source_process):
        manual_id = self.manual.get((source_part, source_process))
        if manual_id is not None:
            item = self.by_id.get(manual_id)
            return (item if item and item.is_active == 'Y' else None), 'MANUAL'
        suffix = self.suffix(source_process)
        if suffix == '?':
            return None, ''
        target = source_part.strip()
        if suffix is not None:
            base = re.sub(r'-(?:ag|a|b|c|d)$', '', target, flags=re.IGNORECASE)
            target = base + suffix
        candidates = self.by_part.get(target.casefold(), [])
        if suffix == '':
            candidates = [item for item in candidates if item.account_type == '완제품']
        if len(candidates) != 1:
            return None, ''
        return candidates[0], 'AUTO_STAGE' if suffix is not None else 'AUTO_EXACT'
