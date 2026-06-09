from ir_search.entity import normalize_entities
from ir_search.models import EntityType, Query


def test_company_alias_and_code_expansion():
    q = normalize_entities(Query(text="中际旭创 300308 Zhongji Innolight 旭创 光模块"))
    ids = {entity.canonical_id for entity in q.entities}
    types = {entity.entity_type for entity in q.entities}

    assert "300308.SZ" in ids
    assert "INDUSTRY:光模块" in ids
    assert EntityType.COMPANY in types
    assert "800G" in q.expanded_terms
    assert "optical module" in q.expanded_terms
