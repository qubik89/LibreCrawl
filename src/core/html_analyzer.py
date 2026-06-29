"""CPU-bound HTML analysis helpers for crawler worker processes."""
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.core.seo_extractor import SEOExtractor


def analyze_html_content(
    html_bytes,
    url,
    depth,
    status_code,
    content_type,
    is_internal,
    base_domain,
    encoding=None,
    persist_links=True,
    allowed_placements=None,
):
    html_text = html_bytes.decode(encoding or 'utf-8', errors='replace')
    soup = BeautifulSoup(html_bytes, 'html.parser')
    result = _base_result(url, depth, status_code, content_type, len(html_bytes), is_internal)

    SEOExtractor.extract_basic_seo_data(soup, result)
    SEOExtractor.extract_meta_tags(soup, result)
    SEOExtractor.extract_opengraph_tags(soup, result)
    SEOExtractor.extract_twitter_tags(soup, result)
    SEOExtractor.extract_json_ld(soup, result)
    SEOExtractor.extract_analytics_tracking(soup, html_text, result)
    SEOExtractor.extract_images(soup, url, result)
    SEOExtractor.extract_link_counts(soup, result, base_domain)
    SEOExtractor.extract_hreflang(soup, result)
    SEOExtractor.extract_schema_org(soup, result)

    return {
        'result': result,
        'links': _collect_links(soup, url, base_domain, allowed_placements) if persist_links else [],
        'discovered_urls': _collect_discovered_urls(soup, url, depth + 1),
    }


def _base_result(url, depth, status_code, content_type, size, is_internal):
    return {
        'url': url,
        'status_code': status_code,
        'error_type': None,
        'content_type': content_type,
        'size': size,
        'is_internal': is_internal,
        'depth': depth,
        'title': '',
        'meta_description': '',
        'h1': '',
        'h2': [],
        'h3': [],
        'word_count': 0,
        'meta_tags': {},
        'og_tags': {},
        'twitter_tags': {},
        'canonical_url': '',
        'lang': '',
        'charset': '',
        'viewport': '',
        'robots': '',
        'author': '',
        'keywords': '',
        'generator': '',
        'theme_color': '',
        'json_ld': [],
        'analytics': {
            'google_analytics': False,
            'gtag': False,
            'ga4_id': '',
            'gtm_id': '',
            'facebook_pixel': False,
            'hotjar': False,
            'mixpanel': False
        },
        'images': [],
        'external_links': 0,
        'internal_links': 0,
        'response_time': 0,
        'redirects': [],
        'hreflang': [],
        'schema_org': [],
        'linked_from': []
    }


def _collect_discovered_urls(soup, current_url, depth):
    urls = []
    for link in soup.find_all('a', href=True):
        href = link['href'].strip()
        if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
            continue
        clean_url = _clean_url(urljoin(current_url, href))
        if clean_url and clean_url != current_url:
            urls.append({'url': clean_url, 'depth': depth})
    return urls


def _collect_links(soup, source_url, base_domain, allowed_placements=None):
    allowed = set(allowed_placements or [])
    links = []

    for link in soup.find_all('a', href=True):
        href = link['href'].strip()
        if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
            continue

        clean_url = _clean_url(urljoin(source_url, href))
        if not clean_url:
            continue

        placement = _detect_link_placement(link)
        if allowed and placement not in allowed:
            continue

        parsed_target = urlparse(clean_url)
        links.append({
            'source_url': source_url,
            'target_url': clean_url,
            'anchor_text': link.get_text().strip()[:100] or '(no text)',
            'is_internal': _same_domain(parsed_target.netloc, base_domain),
            'target_domain': parsed_target.netloc,
            'target_status': None,
            'placement': placement,
        })

    if allowed and 'image' not in allowed:
        return links

    for img in soup.find_all('img', src=True):
        src = img.get('src', '').strip()
        if not src or src.startswith('data:'):
            continue

        clean_url = _clean_url(urljoin(source_url, src))
        if not clean_url:
            continue

        parsed_target = urlparse(clean_url)
        if parsed_target.scheme not in ('http', 'https'):
            continue

        links.append({
            'source_url': source_url,
            'target_url': clean_url,
            'anchor_text': img.get('alt', '').strip()[:100] or '(no alt text)',
            'is_internal': _same_domain(parsed_target.netloc, base_domain),
            'target_domain': parsed_target.netloc,
            'target_status': None,
            'placement': 'image',
        })

    return links


def _clean_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        clean_url += f"?{parsed.query}"
    return clean_url


def _same_domain(domain, base_domain):
    return domain.replace('www.', '', 1) == base_domain.replace('www.', '', 1)


def _detect_link_placement(link_element):
    current = link_element.parent
    while current and current.name:
        if current.name == 'footer':
            return 'footer'

        classes = current.get('class', [])
        element_id = current.get('id', '')
        classes_str = ' '.join(classes).lower() if classes else ''

        if 'footer' in classes_str or 'footer' in element_id.lower():
            return 'footer'
        if current.name in ['nav', 'header']:
            return 'navigation'
        if any(keyword in classes_str or keyword in element_id.lower() for keyword in ['nav', 'menu', 'header']):
            return 'navigation'

        current = current.parent

    return 'body'
