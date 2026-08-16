# Offline self-tests for the igf_pipeline download and extraction modules.
# Run: python tests/test_download.py   (or: python main.py selftest)
# Every network call is monkeypatched, so the suite is fully offline.
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

try:
    from igf_pipeline.models import network, dom, classify, extract, deepcrawl
    from bs4 import BeautifulSoup
    HAS_S = True
except Exception:
    HAS_S = False

Q = chr(34)


class HTTPError(Exception):
    pass


class _FakeResp:
    def __init__(self, status=200, content=b'', url=''):
        self.status_code = status
        self.content = content
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError('HTTPError ' + str(self.status_code))


class _FakeTextResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


class _FakeScraper:
    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def get(self, url, timeout=30):
        self.calls.append(url)
        return self.fn(url)


LONG = ('Internet governance is a multistakeholder process involving governments, business, '
        'civil society and the technical community, with shared principles for an open and secure internet. ')

HTML_OK = ('<html><head><title>IGF 2022 Workshop on Cybersecurity</title></head><body><main>'
           '<div class=' + Q + 'field field--name-field-theme' + Q + '>'
           '<div class=' + Q + 'field__label' + Q + '>Theme</div>'
           '<div class=' + Q + 'field__item' + Q + '>Cybersecurity</div></div>'
           '<h2>Policy questions</h2>'
           '<p>' + LONG * 6 + '</p>'
           '</main></body></html>')

HTML_NEXT = ('<html><body>'
             '<a href=' + Q + '/page2' + Q + ' rel=' + Q + 'next' + Q + '>Next page</a>'
             '<a href=' + Q + '/about' + Q + '>About</a>'
             '<a class=' + Q + 'pager__item next' + Q + ' href=' + Q + '/page3' + Q + '>Pager</a>'
             '</body></html>')


@unittest.skipUnless(HAS_S, 'scrape_igf dependencies not installed')
class DownloadPipelineTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.saved = {}
        for name in ('_visited_urls', '_inflight_urls', '_failed_seen', '_FILE_MAP',
                     '_failed_log_path', '_stats', '_rate_state', '_wb_state'):
            self.saved[name] = getattr(network, name)
        network._visited_urls.clear()
        network._inflight_urls.clear()
        network._failed_seen.clear()
        network._FILE_MAP.clear()
        network._failed_log_path[0] = None
        network._stats.clear()
        network._stats.update({'ok': 0, 'fail': 0, 'skip': 0, 'pages': 0, 'errors': 0})
        network._rate_state['gap'] = 0.0
        network._rate_state['next_ts'] = 0.0
        network._rate_state['cooldown_until'] = 0.0
        network._wb_state['fails'] = 0
        network._wb_state['disabled'] = False
        self.saved_fetch = network._fetch
        self.saved_wb = network._wb_get
        self.saved_scraper = network._get_tl_scraper

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(network, name, value)
        network._fetch = self.saved_fetch
        network._wb_get = self.saved_wb
        network._get_tl_scraper = self.saved_scraper
        self.tmp.cleanup()

    def ok_url(self):
        return 'https://intgovforum.org/en/content/igf-2020-workshop-test'

    def ok_scraper(self):
        return _FakeScraper(lambda url: _FakeResp(200, HTML_OK.encode('utf-8'), url))

    def test_magic_pdf(self):
        self.assertEqual(network._magic_ext(b'%PDF-1.7 rest'), '.pdf')

    def test_magic_docx(self):
        data = b'PK' + b'x' * 10 + b'[Content_Types].xml' + b'y' * 10
        self.assertEqual(network._magic_ext(data), '.docx')

    def test_magic_zip(self):
        self.assertEqual(network._magic_ext(b'PK' + b'z' * 100), '.zip')

    def test_magic_doc(self):
        self.assertEqual(network._magic_ext(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'x'), '.doc')

    def test_magic_unknown(self):
        self.assertIsNone(network._magic_ext(b'plain text'))

    def test_fix_bin_ext_renames_bin(self):
        path = os.path.join(self.root, 'doc.bin')
        with open(path, 'wb') as f:
            f.write(b'%PDF-1.4' + b'x' * 100)
        real = network._fix_bin_ext(path, b'%PDF-1.4' + b'x' * 100)
        self.assertTrue(real.endswith('.pdf'))
        self.assertTrue(os.path.exists(real))
        self.assertFalse(os.path.exists(path))
        self.assertIn((path, real), network._FILE_MAP)

    def test_fix_bin_ext_keeps_named_ext(self):
        path = os.path.join(self.root, 'doc.pdf')
        with open(path, 'wb') as f:
            f.write(b'%PDF-1.4' + b'x' * 100)
        self.assertEqual(network._fix_bin_ext(path, b'%PDF-1.4' + b'x' * 100), path)

    def test_bin_valid_pdf_ok(self):
        self.assertTrue(network._bin_valid('https://x/a.pdf', b'%PDF-1.4', 500))

    def test_bin_valid_pdf_bad(self):
        self.assertFalse(network._bin_valid('https://x/a.pdf', b'hello wo', 500))

    def test_bin_valid_bin_size(self):
        self.assertTrue(network._bin_valid('https://x/a.bin', b'junk', 100))
        self.assertFalse(network._bin_valid('https://x/a.bin', b'junk', 99))

    def test_file_ok_big_html(self):
        path = os.path.join(self.root, 'a.html')
        with open(path, 'wb') as f:
            f.write(b'x' * 500)
        self.assertTrue(network._file_ok('https://x/a.html', path))

    def test_file_ok_small_html(self):
        path = os.path.join(self.root, 'a.html')
        with open(path, 'wb') as f:
            f.write(b'x' * 100)
        self.assertFalse(network._file_ok('https://x/a.html', path))

    def test_file_ok_good_pdf(self):
        path = os.path.join(self.root, 'a.pdf')
        with open(path, 'wb') as f:
            f.write(b'%PDF-1.4' + b'x' * 300)
        self.assertTrue(network._file_ok('https://x/a.pdf', path))

    def test_file_ok_bad_pdf(self):
        path = os.path.join(self.root, 'a.pdf')
        with open(path, 'wb') as f:
            f.write(b'not a pdf' + b'x' * 100)
        self.assertFalse(network._file_ok('https://x/a.pdf', path))

    def test_atomic_write_content(self):
        path = os.path.join(self.root, 'sub', 'a.html')
        network._atomic_write_bytes(path, b'hello-world')
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), b'hello-world')

    def test_no_part_leftover(self):
        path = os.path.join(self.root, 'sub', 'a.html')
        network._atomic_write_bytes(path, b'hello-world')
        leftovers = [p for p in os.listdir(os.path.dirname(path)) if p.endswith('.part')]
        self.assertEqual(leftovers, [])

    def test_download_one_ok(self):
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        result = network._download_one(self.ok_scraper(), self.ok_url(), path)
        self.assertEqual(result, 'ok')
        self.assertTrue(os.path.exists(path))

    def test_download_one_wrote_file(self):
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        network._download_one(self.ok_scraper(), self.ok_url(), path)
        with open(path, 'rb') as f:
            self.assertGreater(len(f.read()), 300)

    def test_visited_after_success_scoped(self):
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        network._download_one(self.ok_scraper(), self.ok_url(), path)
        key = (network._norm_url(self.ok_url()), network._scope_key(path))
        self.assertIn(key, network._visited_urls)

    def test_inflight_cleared(self):
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        network._download_one(self.ok_scraper(), self.ok_url(), path)
        self.assertEqual(len(network._inflight_urls), 0)

    def test_download_one_fail(self):
        network._wb_get = lambda *a, **k: None
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        scraper = _FakeScraper(lambda url: _FakeResp(500, b'server error', url))
        result = network._download_one(scraper, self.ok_url(), path)
        self.assertEqual(result, 'fail')
        self.assertFalse(os.path.exists(path))

    def test_fail_not_visited(self):
        network._wb_get = lambda *a, **k: None
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        scraper = _FakeScraper(lambda url: _FakeResp(500, b'server error', url))
        network._download_one(scraper, self.ok_url(), path)
        key = (network._norm_url(self.ok_url()), network._scope_key(path))
        self.assertNotIn(key, network._visited_urls)

    def test_fail_recorded(self):
        network._wb_get = lambda *a, **k: None
        log = os.path.join(self.root, 'failed_urls.tsv')
        network._failed_log_path[0] = log
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        scraper = _FakeScraper(lambda url: _FakeResp(500, b'server error', url))
        network._download_one(scraper, self.ok_url(), path)
        with open(log, 'r', encoding='utf-8') as f:
            self.assertIn(self.ok_url(), f.read())

    def test_download_one_429_retry_ok(self):
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        calls = [0]

        def fn(url):
            calls[0] += 1
            if calls[0] == 1:
                return _FakeResp(429, b'rate limited', url)
            return _FakeResp(200, HTML_OK.encode('utf-8'), url)

        saved_sleep = network.time.sleep
        network.time.sleep = lambda s: None
        try:
            result = network._download_one(_FakeScraper(fn), self.ok_url(), path)
        finally:
            network.time.sleep = saved_sleep
        self.assertEqual(result, 'ok')
        self.assertTrue(os.path.exists(path))
        self.assertGreaterEqual(calls[0], 2)

    def test_download_one_403_retry_ok(self):
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        calls = [0]

        def fn(url):
            calls[0] += 1
            if calls[0] == 1:
                return _FakeResp(403, b'forbidden', url)
            return _FakeResp(200, HTML_OK.encode('utf-8'), url)

        saved_sleep = network.time.sleep
        network.time.sleep = lambda s: None
        try:
            result = network._download_one(_FakeScraper(fn), self.ok_url(), path)
        finally:
            network.time.sleep = saved_sleep
        self.assertEqual(result, 'ok')
        self.assertTrue(os.path.exists(path))
        self.assertGreaterEqual(calls[0], 2)

    def test_download_one_5xx_http_error_retries(self):
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        calls = [0]

        class _Resp500WithResponse:
            status_code = 500
            content = b'server error'

            def raise_for_status(self):
                err = HTTPError('HTTPError 500')
                err.response = _FakeResp(500, b'server error')
                raise err

        def fn(url):
            calls[0] += 1
            if calls[0] < 3:
                return _Resp500WithResponse()
            return _FakeResp(200, HTML_OK.encode('utf-8'), url)

        saved_sleep = network.time.sleep
        network.time.sleep = lambda s: None
        try:
            result = network._download_one(_FakeScraper(fn), self.ok_url(), path)
        finally:
            network.time.sleep = saved_sleep
        self.assertEqual(result, 'ok')
        self.assertEqual(calls[0], 3)

    def test_fail_reason_records_status_code(self):
        network._wb_get = lambda *a, **k: None
        log = os.path.join(self.root, 'failed_urls.tsv')
        network._failed_log_path[0] = log
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        scraper = _FakeScraper(lambda url: _FakeResp(404, b'not found', url))
        network._download_one(scraper, self.ok_url(), path)
        with open(log, 'r', encoding='utf-8') as f:
            self.assertIn('HTTP 404', f.read())

    def test_wayback_fallback_ok(self):
        path = os.path.join(self.root, '02_reports', 'report.pdf')
        url = 'https://intgovforum.org/sites/default/files/report.pdf'
        scraper = _FakeScraper(lambda u: _FakeResp(404, b'not found', u))
        pdf = b'%PDF-1.4' + b'x' * 300
        network._wb_get = lambda u, timeout=20, scraper=None: _FakeResp(200, pdf, u)
        result = network._download_one(scraper, url, path)
        self.assertEqual(result, 'ok')
        with open(path, 'rb') as f:
            self.assertTrue(f.read().startswith(b'%PDF-'))

    def test_batch_success_rate_printed(self):
        network._get_tl_scraper = lambda: self.ok_scraper()
        path = os.path.join(self.root, '01_sessions', 'igf-2020-workshop-test.html')
        buf = io.StringIO()
        with redirect_stdout(buf):
            network._download_batch([(self.ok_url(), path, None)], workers=2)
        out = buf.getvalue()
        self.assertIn('pass1', out)
        self.assertIn('success=100.0%', out)
        self.assertTrue(os.path.exists(path))

    def test_same_url_two_folders_both_ok(self):
        p1 = os.path.join(self.root, 'A', 'igf-2020-workshop-test.html')
        p2 = os.path.join(self.root, 'B', 'igf-2020-workshop-test.html')
        self.assertEqual(network._download_one(self.ok_scraper(), self.ok_url(), p1), 'ok')
        self.assertEqual(network._download_one(self.ok_scraper(), self.ok_url(), p2), 'ok')
        self.assertTrue(os.path.exists(p1))
        self.assertTrue(os.path.exists(p2))

    def test_same_url_same_folder_second_skip(self):
        p1 = os.path.join(self.root, 'C', 'igf-2020-workshop-test.html')
        self.assertEqual(network._download_one(self.ok_scraper(), self.ok_url(), p1), 'ok')
        self.assertEqual(network._download_one(self.ok_scraper(), self.ok_url(), p1), 'skip')

    def test_pagination_next_rel_found(self):
        soup = BeautifulSoup(HTML_NEXT, 'html.parser')
        links = dom._next_page_links(soup, 'https://intgovforum.org/en/list')
        self.assertIn('https://intgovforum.org/page2', links)
        self.assertIn('https://intgovforum.org/page3', links)

    def test_pagination_normal_link_excluded(self):
        soup = BeautifulSoup(HTML_NEXT, 'html.parser')
        links = dom._next_page_links(soup, 'https://intgovforum.org/en/list')
        self.assertNotIn('https://intgovforum.org/about', links)

    def test_strip_noise_removes_script_and_nav(self):
        html = ('<html><body><nav>menu</nav><script>bad()</script>'
                '<main><p>keep me</p></main></body></html>')
        soup = BeautifulSoup(html, 'html.parser')
        dom._strip_noise(soup)
        self.assertIsNone(soup.find('nav'))
        self.assertIsNone(soup.find('script'))
        self.assertIsNotNone(soup.find('main'))

    def test_strip_noise_keeps_drupal_field(self):
        html = ('<html><body><main><div class=' + Q + 'field field--name-field-theme' + Q + '>'
                '<div class=' + Q + 'field__item' + Q + '>Cybersecurity</div></div></main></body></html>')
        soup = BeautifulSoup(html, 'html.parser')
        dom._strip_noise(soup)
        self.assertIsNotNone(soup.select_one('.field--name-field-theme'))

    def test_extract_drupal_fields_theme(self):
        html = ('<div class=' + Q + 'field field--name-field-theme' + Q + '>'
                '<div class=' + Q + 'field__label' + Q + '>Theme</div>'
                '<div class=' + Q + 'field__item' + Q + '>Cybersecurity</div></div>')
        soup = BeautifulSoup(html, 'html.parser')
        fields = dom._extract_drupal_fields_json(soup)
        self.assertIn('theme', fields)
        self.assertEqual(fields['theme']['label'], 'Theme')
        self.assertEqual(fields['theme']['content'][0]['text'], 'Cybersecurity')

    def test_classify_content_workshop(self):
        html = ('<html><head><title>IGF 2022 Workshop Cybersecurity</title></head>'
                '<body><main><p>' + LONG * 3 + '</p></main></body></html>')
        self.assertEqual(classify._classify_by_content(html), 'workshop')

    def test_classify_content_day0(self):
        html = ('<html><head><title>IGF 2022 Day 0 Event orientation</title></head>'
                '<body><main><p>' + LONG * 3 + '</p></main></body></html>')
        self.assertEqual(classify._classify_by_content(html), 'day-0-event')

    def test_extract_year_from_text(self):
        self.assertEqual(network._year_from_text('igf_full_2017_workshops/x.html'), '2017')
        self.assertIsNone(network._year_from_text('no year here'))

    def test_extract_one_file_record(self):
        src_root = os.path.join(self.root, 'classified')
        sub = os.path.join(src_root, 'workshop')
        os.makedirs(sub, exist_ok=True)
        path = os.path.join(sub, 'igf-2022-workshop-test.html')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(HTML_OK)
        rec = extract._extract_one_file(path, src_root)
        self.assertIsNotNone(rec)
        self.assertEqual(rec['type'], 'workshop')
        self.assertEqual(rec['year'], 2022)
        self.assertIn('Cybersecurity', rec['title'])
        self.assertEqual(rec['quality'], 'ok')
        self.assertIn('theme', rec['drupal_fields'])
        self.assertGreater(len(rec['body_text']), 100)
        self.assertIn('Policy questions', rec['headings'])

    def test_extract_skips_junk_page(self):
        src_root = os.path.join(self.root, 'classified')
        os.makedirs(src_root, exist_ok=True)
        tiny = os.path.join(src_root, 'tiny.html')
        with open(tiny, 'w', encoding='utf-8') as f:
            f.write('<html><body>short</body></html>')
        self.assertIsNone(extract._extract_one_file(tiny, src_root))
        empty = os.path.join(src_root, 'igf-2022-empty.html')
        with open(empty, 'w', encoding='utf-8') as f:
            f.write('<html><body><main><div></div></main></body></html>')
        self.assertIsNone(extract._extract_one_file(empty, src_root))

    def test_deep_crawl_seed_and_pagination(self):
        network._get_tl_scraper = lambda: None
        seed = 'https://intgovforum.org/en/archived/igf-2020'
        p2 = 'https://intgovforum.org/page2'
        pages = {
            seed: _FakeTextResp(HTML_NEXT),
            p2: _FakeTextResp('<html><body><main><p>' + LONG * 3 + '</p></main></body></html>'),
        }
        network._fetch = lambda url, wb_year=None: pages.get(url)
        out_dir = os.path.join(self.root, '05_archived', '2020')
        deepcrawl._deep_crawl_parallel(seed, out_dir, workers=2)
        self.assertTrue(os.path.exists(os.path.join(out_dir, 'igf-2020.html')))
        self.assertTrue(os.path.exists(os.path.join(out_dir, 'page2.html')))

    def test_norm_url_strips_www_and_slash(self):
        self.assertEqual(network._norm_url('https://www.intgovforum.org/en/content/x/'),
                         'https://intgovforum.org/en/content/x')


if __name__ == '__main__':
    unittest.main(verbosity=2)


