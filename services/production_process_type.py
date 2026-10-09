"""생산실적 화면에서 사용할 공정 구분."""
from services.production_sync_mapping import stage_suffix


def performance_type_for_process(process):
    suffix = stage_suffix(process.process_name)
    if suffix == '-C':
        return 'ASSEMBLY'
    if suffix in ('-A', '-B', '-D'):
        return 'MACHINING'
    return None
