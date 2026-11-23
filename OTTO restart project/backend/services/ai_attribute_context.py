"""Bounded image-attribute context; filled fields are never suggestion targets."""
import json


def attribute_context(*, definitions, existing_values, source_title, category, image_url, max_input_bytes):
    filled={str(name).strip().casefold() for name,value in existing_values.items() if str(value).strip()}
    candidates=[item for item in definitions if str(item['name']).strip().casefold() not in filled]
    candidates.sort(key=lambda item:(not bool(item.get('required') or item.get('legal')), not bool(item.get('recommended')), str(item['name'])))
    context={'__image_url':image_url,'sourceTitle':source_title[:500],'category':category,
             'existingSourceAttributes':[{'name':str(k)[:120],'value':str(v)[:120]}
                  for k,v in list(existing_values.items())[:60] if str(v).strip()],
             'attributeSchema':[], 'allowedAttributeNames':[]}
    # Reserve envelope, instructions, output schema and serializer overhead.
    budget=max_input_bytes-8000
    def fits():
        return len(json.dumps(context,ensure_ascii=False,separators=(',',':')).encode())<=budget
    while context['existingSourceAttributes'] and not fits():
        context['existingSourceAttributes'].pop()
    for item in candidates:
        definition={'name':item['name'],'type':item.get('type') or 'STRING'}
        if item.get('unit'):
            definition['unit']=item['unit']
        for key in ('required','recommended','legal','multiValue','allowedValues'):
            if key in item:
                definition[key]=item.get(key)
        context['attributeSchema'].append(definition)
        context['allowedAttributeNames'].append(item['name'])
        if not fits():
            context['attributeSchema'].pop();context['allowedAttributeNames'].pop()
    return context
