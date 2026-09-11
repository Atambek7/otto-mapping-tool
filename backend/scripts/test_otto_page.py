import urllib.request
import re

req = urllib.request.Request('https://www.otto.de/p/S09FY0HY', headers={
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
})
try:
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode('utf-8', errors='ignore')
        print('Final URL:', resp.geturl())
        images = re.findall(r'https://i\.otto\.de/i/otto/[^\s"\'<>]+', html)
        print('Found images:', len(images))
        if images:
            for img in images[:5]:
                print(' -', img)
except Exception as e:
    print('Error:', e)
