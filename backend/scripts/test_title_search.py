import asyncio
import httpx
from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL

async def test():
    session = await get_aftercool_session()
    queries = ['Tantrasofa', 'Kristallleuchter Gold', 'Beistelltische Holz', 'Wohnlandschaft echtem Leder', 'Fußbodenheizung']
    async with httpx.AsyncClient(cookies={'session': session}, timeout=15) as c:
        for q in queries:
            r = await c.get(f'{AFTERCOOL_BASE_URL}/api/products', params={'q': q, 'include_row': 1, 'limit': 2})
            items = r.json().get('items', [])
            print(f'Query "{q}": found={len(items)}')
            if items:
                row = items[0].get('row', {})
                img = row.get('PictureURLs') or row.get('PictureURL') or row.get('GalleryURL') or ''
                if isinstance(img, list): img = img[0]
                elif '|' in img: img = img.split('|')[0]
                print(f'   -> {img[:70]}...')

if __name__ == '__main__':
    asyncio.run(test())
