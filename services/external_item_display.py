"""Display current MES master names without rewriting imported evidence."""


def item_display(item, raw):
    source_name = raw.get('PRODUCT_NM', '') or ''
    return {'part_name': (item.part_name or '') if item is not None else source_name,
            'source_part_name': source_name, 'linked': item is not None,
            'name_source': 'MES' if item is not None else 'EXTERNAL'}
