"""Use equipment master identities and human-readable names in daily reports."""
import re
from models.models import ProcessModel
from models.equipment import EquipmentMaster
from services.production_sync_mapping import stage_suffix


def number(value):
    match = re.search(r'\d+', str(value or ''))
    return str(int(match.group())) if match else ''


class DailyReportEquipment:
    def __init__(self, db):
        self.processes = list(db.query(ProcessModel).order_by(ProcessModel.sort_order, ProcessModel.id))
        self.names = {p.process_code: p.process_name for p in self.processes}
        self.equipment = list(db.query(EquipmentMaster).order_by(EquipmentMaster.sort_order, EquipmentMaster.id))
        self.by_id = {e.id: e for e in self.equipment}
        self.by_code = {e.equipment_code: e for e in self.equipment}
        self.by_number = {}
        for e in self.equipment:
            key = (e.process_code, number(e.machine_no))
            if key not in self.by_number or (e.is_active == 'Y' and self.by_number[key].is_active != 'Y'):
                self.by_number[key] = e

    def readable(self, name, code, process, machine):
        name = str(name or '').strip()
        if name and name != code and not re.fullmatch(r'[A-Z]+[-_]?\d*', name):
            return name
        return (self.names.get(process, '생산설비') + ' ' + machine + '호기') if machine else '미확인'

    def label(self, process, current='', equipment_id=None, equipment_code='', machine_no=''):
        equipment = self.by_id.get(equipment_id) or self.by_code.get(equipment_code or current)
        if equipment and equipment.process_code != process:
            equipment = None
        machine = number(machine_no) or number(equipment_code) or number(current)
        equipment = equipment or self.by_number.get((process, machine))
        if equipment:
            equipment = self.by_number.get((process, number(equipment.machine_no)), equipment)
            return self.readable(equipment.equipment_name, equipment.equipment_code, process, number(equipment.machine_no))
        return self.readable(current, equipment_code, process, machine)

    def options(self):
        codes = {e.process_code for e in self.equipment if e.is_active == 'Y'}
        processes = [p.process_name for p in self.processes if p.is_active == 'Y' and
                     (p.process_code in codes or stage_suffix(p.process_name) in {'-A','-B','-C','-D'})]
        equipment, seen = [], set()
        for e in self.equipment:
            process = self.names.get(e.process_code)
            key = (e.process_code, number(e.machine_no))
            if e.is_active != 'Y' or process not in processes or key in seen:
                continue
            seen.add(key)
            name = self.label(e.process_code, equipment_id=e.id)
            if not any(row['process'] == process and row['name'] == name for row in equipment):
                equipment.append({'process': process, 'name': name})
        return {'processes': list(dict.fromkeys(processes)), 'equipments': equipment}
