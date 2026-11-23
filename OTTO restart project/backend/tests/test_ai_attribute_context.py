import json
from services.ai_attribute_context import attribute_context


def test_large_category_fits_budget_and_never_targets_filled_fields():
    definitions=[{'name':f'Field{i}','required':i<6,'allowedValues':['x'*100]*20} for i in range(228)]
    result=attribute_context(definitions=definitions,existing_values={'field0':'already filled'},source_title='Sofa',
        category='Sofa',image_url='https://example.com/image.jpg',max_input_bytes=32000)
    assert 'Field0' not in result['allowedAttributeNames']
    assert result['allowedAttributeNames'][0]=='Field1'
    assert len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())<=24000
    assert result['attributeSchema']


def test_all_filled_returns_no_targets():
    result=attribute_context(definitions=[{'name':'Farbe'}],existing_values={'Farbe':'Rot'},source_title='Sofa',
        category='Sofa',image_url='https://example.com/a.jpg',max_input_bytes=32000)
    assert result['allowedAttributeNames']==[]


def test_attribute_schema_includes_type_and_unit():
    definitions=[
        {'name':'Höhe Füße','type':'FLOAT','unit':'cm','required':False},
        {'name':'Anzahl Sitzflächen','type':'INTEGER','required':True},
        {'name':'Farbe','required':False},
    ]
    result=attribute_context(definitions=definitions,existing_values={},source_title='Sofa',
        category='Sofa',image_url='https://example.com/a.jpg',max_input_bytes=32000)
    by_name={item['name']:item for item in result['attributeSchema']}
    assert by_name['Höhe Füße']['type']=='FLOAT'
    assert by_name['Höhe Füße']['unit']=='cm'
    assert by_name['Anzahl Sitzflächen']['type']=='INTEGER'
    assert 'unit' not in by_name['Anzahl Sitzflächen']
    assert by_name['Farbe']['type']=='STRING'
