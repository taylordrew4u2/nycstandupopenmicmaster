"""Build the standalone, fictional-data preview from the live frontend assets."""
from pathlib import Path
from urllib.parse import quote

root = Path(__file__).resolve().parents[1]
public = root / 'public'
html = (public / 'index.html').read_text()
html = html.replace('<link rel="stylesheet" href="/assets/styles.css">', '<style>' + (public / 'styles.css').read_text() + '</style>')
for asset in ('map.js', 'app.js'):
    html = html.replace(f'<script src="/assets/{asset}" defer></script>', '')
icon = 'data:image/svg+xml,' + quote((public / 'favicon.svg').read_text())
html = html.replace('/assets/favicon.svg', icon)
scripts = '<script>window.MICLIST_PREVIEW=true;</script>'
for asset in ('map.js', 'app.js'):
    scripts += '<script>' + (public / asset).read_text().replace('</script', '<\\/script') + '</script>'
html = html.replace('</body>', scripts + '\n</body>')
(root / 'preview.html').write_text(html)
print(root / 'preview.html')
