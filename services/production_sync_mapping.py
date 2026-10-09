"""Resolve external production items using BOM relationships and stage suffixes."""
import re

from models.models import ItemBomModel, ItemMasterModel, ProcessModel
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


def is_finished(item):
    # Current DB stores product account PROD and material FINISHED separately.
    return item.material_type in ('FINISHED', '완제품') or item.account_type == '완제품'


class ItemConnections:
    def __init__(self, db):
        self.items = db.query(ItemMasterModel).all()
        self.by_id = {item.id: item for item in self.items}
        self.by_part = {}
        for item in self.items:
            if item.is_active == 'Y':
                self.by_part.setdefault(item.part_no.casefold(), []).append(item)
        self.children, self.parents = {}, {}
        self.bom_cache = {}
        for row in db.query(ItemBomModel):
            parent_item, child_item = self.by_id.get(row.parent_item_id), self.by_id.get(row.child_item_id)
            parent = (parent_item.part_no if parent_item else row.parent_part_no).strip().casefold()
            child = (child_item.part_no if child_item else row.child_part_no).strip().casefold()
            self.children.setdefault(parent, set()).add(child)
            self.parents.setdefault(child, set()).add(parent)
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
        if len(candidates) == 1:
            return candidates[0]
        if source_process in self.processes:
            return source_process
        suffix = stage_suffix(source_process)
        if suffix is None or suffix == '?':
            return ''
        candidates = [code for code, name in self.processes.items() if stage_suffix(name) == suffix]
        return candidates[0] if len(candidates) == 1 else ''

    def suffix(self, source_process):
        return stage_suffix(self.processes.get(self.process_code(source_process), ''), source_process)

    def bom_candidates(self, source_part, suffix):
        if suffix is None or suffix == '?':
            return [], False
        original = source_part.strip().casefold()
        if not original:
            return [], False
        if (original, suffix) in self.bom_cache:
            return self.bom_cache[original, suffix]
        base = re.sub(r'-(?:ag|a|b|c|d)$', '', original)
        roots = {original, base}
        # Production stages are descendants of a finished item's BOM. Packing
        # reverses the lookup: an assembly item may belong to a different-number
        # finished parent. Walk all levels and guard against malformed cycles.
        seen, frontier = set(roots), list(roots)
        while frontier:
            for related in self.parents.get(frontier.pop(), ()):
                if related not in seen:
                    seen.add(related); frontier.append(related)
        if suffix != '':
            frontier = list(seen)
            while frontier:
                for related in self.children.get(frontier.pop(), ()):
                    if related not in seen:
                        seen.add(related); frontier.append(related)
        has_bom = any(root in self.children or root in self.parents for root in roots)
        candidates = {}
        for part in seen:
            if part in roots and part not in self.children and part not in self.parents:
                continue
            for item in self.by_part.get(part, []):
                matches = (is_finished(item) if suffix == ''
                           else item.part_no.casefold().endswith(suffix.casefold()))
                if matches:
                    candidates[item.id] = item
        result = list(candidates.values()), has_bom
        self.bom_cache[original, suffix] = result
        return result

    def resolve(self, source_part, source_process):
        manual_id = self.manual.get((source_part, source_process))
        if manual_id is not None:
            item = self.by_id.get(manual_id)
            return (item if item and item.is_active == 'Y' else None), 'MANUAL'
        suffix = self.suffix(source_process)
        if suffix == '?':
            return None, ''
        candidates, has_bom = self.bom_candidates(source_part, suffix)
        if has_bom:
            return (candidates[0], 'AUTO_BOM') if len(candidates) == 1 else (None, '')
        target = source_part.strip()
        if suffix is not None:
            base = re.sub(r'-(?:ag|a|b|c|d)$', '', target, flags=re.IGNORECASE)
            target = base + suffix
        candidates = self.by_part.get(target.casefold(), [])
        if suffix == '':
            candidates = [item for item in candidates if is_finished(item)]
        if len(candidates) != 1:
            return None, ''
        return candidates[0], 'AUTO_STAGE' if suffix is not None else 'AUTO_EXACT'
