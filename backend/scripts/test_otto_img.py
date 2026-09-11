import httpx
import re

url = 'https://www.otto.de/p/S03H002U/'
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
try:
    res = httpx.get(url, headers=headers, follow_redirects=True, timeout=10)
    print('Otto shop status:', res.status_code)
    m = re.search(r'<meta property="og:image" content="([^"]+)"', res.text)
    if m:
        print('og:image:', m.group(1))
    else:
        m2 = re.search(r'"imageUrl":"([^"]+)"', res.text)
        print('imageUrl in json:', m2.group(1) if m2 else 'Not found')
        # check all image urls in page
        imgs = re.findall(r'https://i\.otto\.de/i/otto/[a-zA-Z0-9_-]+', res.text)
        print('otto images found:', imgs[:3])
except Exception as e:
    print('Error:', e)
