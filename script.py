# -*- coding: utf-8 -*-

import ui
import dialogs
import requests
import clipboard
import console
import threading
import keychain
import random
import re
import time
import os
import shutil
import json
import uuid
import datetime
from ctypes import Structure, c_double, c_void_p, c_int
from objc_util import (ObjCClass, on_main_thread, nsurl,
                       ObjCInstance, c)

CHAR_W_FACTOR = 0.55
LINE_H_FACTOR = 1.7


class CGPoint(Structure):
    _fields_ = [('x', c_double), ('y', c_double)]


class CGSize(Structure):
    _fields_ = [('width', c_double), ('height', c_double)]


class CGRect(Structure):
    _fields_ = [('origin', CGPoint), ('size', CGSize)]


def CGSizeMake(w, h):
    return CGSize(w, h)


def CGRectMake(x, y, w, h):
    return CGRect(CGPoint(x, y), CGSize(w, h))


LIBRARY_PATH = os.path.join(
    os.path.expanduser('~/Documents'), 'quotes_library.json')


def load_library():
    if not os.path.exists(LIBRARY_PATH):
        return []
    try:
        with open(LIBRARY_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_library(quotes):
    try:
        with open(LIBRARY_PATH, 'w', encoding='utf-8') as f:
            json.dump(quotes, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def extract_random_text_from_pdf(pdf_path, max_pages=30):
    if not pdf_path or not os.path.exists(pdf_path):
        raise Exception('الملف غير موجود.')
    PDFDocument = ObjCClass('PDFDocument')
    file_url = nsurl(pdf_path)
    doc = PDFDocument.alloc().initWithURL_(file_url)
    if not doc:
        raise Exception('تعذر فتح PDF.')
    page_count = doc.pageCount()
    if page_count == 0:
        return ''
    if page_count > max_pages:
        start_page = random.randint(0, page_count - max_pages)
    else:
        start_page = 0
    end_page = min(start_page + max_pages, page_count)
    all_text = []
    for i in range(start_page, end_page):
        page = doc.pageAtIndex_(i)
        if page:
            raw = page.string()
            page_str = str(raw) if raw is not None else ''
            if page_str and page_str.strip() and page_str.strip() != 'None':
                all_text.append('[صفحة ' + str(i + 1) + ']\n' + page_str.strip())
    return '\n\n'.join(all_text)


def clean_pdf_text_for_prompt(text):
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]+', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    text = re.sub(r'[\u0300-\u036f\u1dc0-\u1dff\u20d0-\u20ff\ufe00-\ufe0f]', '', text)
    text = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def clean_output_text(text):
    text = text.replace('<unk>', '').replace('[UNK]', '').replace('<UNKNOWN>', '')
    if '```' in text:
        text = re.sub(r'```[a-zA-Z]*\n', '', text)
        text = text.replace('```', '')
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]+', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    text = re.sub(r'[^\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF0-9\s\.\,\!\?\-\:\;\(\)\[\]\«\»\u060C\u061B\u061F\u066A-\u066D]', '', text)
    lines = text.split('\n')
    filtered = []
    for line in lines:
        s = line.strip()
        if not s:
            filtered.append('')
            continue
        has_arabic = bool(re.search(r'[\u0600-\u06FF]', s))
        is_marker = bool(re.match(r'^\[?صفحة|^\d+\s*[\-\.\]\)]|^\d+$', s))
        if has_arabic or is_marker:
            filtered.append(line)
    result = '\n'.join(filtered).strip()
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result


def truncate_at_line(text, max_chars):
    if len(text) <= max_chars:
        return text
    cut = text.rfind('\n', 0, max_chars)
    return text[:cut if cut > 0 else max_chars]


def parse_quotes(text):
    text = text.strip()
    if not text:
        return []
    parts = re.split(r'\n(?=\s*\d+\s*[\.\-–—\)])', '\n' + text)
    quotes = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        p = re.sub(r'^\s*\d+\s*[\.\-–—\)]\s*', '', p)
        if p and len(p) > 20:
            quotes.append(p)
    if len(quotes) <= 1 and len(text) > 400:
        chunks = [c.strip() for c in re.split(r'\n\n+', text) if c.strip()]
        if len(chunks) > 1:
            return chunks
    return quotes if quotes else [text]


def estimate_label_height(text, width, font_size=14):
    char_width = font_size * CHAR_W_FACTOR
    line_height = font_size * LINE_H_FACTOR
    chars_per_line = max(1, int((width - 20) / char_width))
    lines = 0
    for para in text.split('\n'):
        if not para:
            lines += 1
        else:
            lines += max(1, -(-len(para) // chars_per_line))
    return max(30, int(lines * line_height) + 15)


def _get_top_vc():
    UIApplication = ObjCClass('UIApplication')
    app = UIApplication.sharedApplication()
    root = None
    try:
        scenes = app.connectedScenes()
        for scene in scenes:
            if int(scene.activationState()) != 0:
                continue
            for w in scene.windows():
                if w.isKeyWindow():
                    root = w.rootViewController()
                    break
            if root:
                break
    except Exception:
        pass
    if root is None:
        try:
            for w in app.windows():
                if w.isKeyWindow():
                    root = w.rootViewController()
                    break
        except Exception:
            pass
    if root is None:
        return None
    while root.presentedViewController():
        root = root.presentedViewController()
    return root


def share_text_ios(text):
    try:
        root = _get_top_vc()
        if root is None:
            return 'تعذر الوصول إلى الواجهة'
        UIActivityViewController = ObjCClass('UIActivityViewController')
        NSArray = ObjCClass('NSArray')
        NSString = ObjCClass('NSString')
        items = NSArray.arrayWithObject_(NSString.stringWithString_(text))
        vc = UIActivityViewController.alloc().initWithActivityItems_applicationActivities_(
            items, None)
        pop = vc.popoverPresentationController()
        if pop:
            pop.setSourceView_(root.view())
            b = root.view().bounds()
            pop.setSourceRect_((b.size.width / 2, b.size.height / 2, 1, 1))
            pop.setPermittedArrowDirections_(0)
        root.presentViewController_animated_completion_(vc, True, None)
        return True
    except Exception as e:
        return 'تعذر فتح المشاركة: ' + str(e)


def share_image_file(image_path):
    try:
        root = _get_top_vc()
        if root is None:
            return 'تعذر الوصول إلى الواجهة'
        UIActivityViewController = ObjCClass('UIActivityViewController')
        NSArray = ObjCClass('NSArray')
        NSURL = ObjCClass('NSURL')
        url = NSURL.fileURLWithPath_(image_path)
        items = NSArray.arrayWithObject_(url)
        vc = UIActivityViewController.alloc().initWithActivityItems_applicationActivities_(
            items, None)
        pop = vc.popoverPresentationController()
        if pop:
            pop.setSourceView_(root.view())
            b = root.view().bounds()
            pop.setSourceRect_((b.size.width / 2, b.size.height / 2, 1, 1))
            pop.setPermittedArrowDirections_(0)
        root.presentViewController_animated_completion_(vc, True, None)
        return True
    except Exception as e:
        return 'تعذر فتح المشاركة: ' + str(e)


def render_quote_image(quote_text, book_text):
    """رسم الاقتباس على صورة PNG باستخدام UIBezierPath + NSString."""
    try:
        w, h = 1080, 1350

        _begin = c.UIGraphicsBeginImageContextWithOptions
        _begin.argtypes = [CGSize, c_int, c_double]
        _begin.restype = None

        _get_img = c.UIGraphicsGetImageFromCurrentImageContext
        _get_img.restype = c_void_p

        _end = c.UIGraphicsEndImageContext
        _end.restype = None

        _png = c.UIImagePNGRepresentation
        _png.argtypes = [c_void_p]
        _png.restype = c_void_p

        UIColor = ObjCClass('UIColor')
        UIFont = ObjCClass('UIFont')
        NSString = ObjCClass('NSString')
        NSMutableParagraphStyle = ObjCClass('NSMutableParagraphStyle')
        UIBezierPath = ObjCClass('UIBezierPath')

        result = [None]

        @on_main_thread
        def _render():
            try:
                _begin(CGSize(w, h), 1, 1.0)

                # الخلفية الكريمية عبر UIBezierPath (يقبل CGRect بشكل صحيح)
                bg = UIColor.colorWithRed_green_blue_alpha_(
                    0.99, 0.98, 0.95, 1.0)
                bg.setFill()
                UIBezierPath.bezierPathWithRect_(
                    CGRectMake(0, 0, w, h)).fill()

                # نمط الفقرة (يمين)
                para = NSMutableParagraphStyle.alloc().init()
                para.setAlignment_(2)
                para.setLineSpacing_(16)

                # علامة الاقتباس ❝
                mark_attrs = {
                    'NSFont': UIFont.systemFontOfSize_(200),
                    'NSColor': UIColor.colorWithRed_green_blue_alpha_(
                        0.17, 0.42, 0.69, 1.0),
                    'NSParagraphStyle': para,
                }
                mark_ns = NSString.stringWithString_('\u201C')
                mark_ns.drawInRect_withAttributes_(
                    CGRectMake(70, 40, 300, 260), mark_attrs)

                # نص الاقتباس
                body_attrs = {
                    'NSFont': UIFont.systemFontOfSize_(44),
                    'NSColor': UIColor.colorWithRed_green_blue_alpha_(
                        0.10, 0.10, 0.14, 1.0),
                    'NSParagraphStyle': para,
                }
                quote_ns = NSString.stringWithString_(quote_text)
                quote_ns.drawInRect_withAttributes_(
                    CGRectMake(90, 300, w - 180, h - 600), body_attrs)

                # اسم الكتاب أسفل
                if book_text:
                    foot_attrs = {
                        'NSFont': UIFont.systemFontOfSize_(26),
                        'NSColor': UIColor.colorWithRed_green_blue_alpha_(
                            0.45, 0.45, 0.5, 1.0),
                        'NSParagraphStyle': para,
                    }
                    book_ns = NSString.stringWithString_(book_text)
                    book_ns.drawInRect_withAttributes_(
                        CGRectMake(90, h - 210, w - 180, 130), foot_attrs)

                img_ptr = _get_img()
                _end()

                if not img_ptr:
                    result[0] = 'ERR: صورة فارغة'
                    return

                data_ptr = _png(img_ptr)
                if not data_ptr:
                    result[0] = 'ERR: PNG فشل'
                    return

                data = ObjCInstance(data_ptr)
                out = os.path.join(
                    os.path.expanduser('~/Documents'),
                    'quote_' + str(int(time.time())) + '.png')
                ok = data.writeToFile_atomically_(out, True)
                result[0] = out if ok else 'ERR: حفظ فشل'
            except Exception as ex:
                try:
                    _end()
                except Exception:
                    pass
                result[0] = 'ERR: ' + str(ex)

        _render()
        return result[0] if result[0] else 'ERR: نتيجة فارغة'

    except Exception as e:
        return 'ERR: ' + str(e)


def get_free_arabic_models():
    try:
        r = requests.get('https://openrouter.ai/api/v1/models', timeout=15)
        if r.status_code != 200:
            return []
        models = r.json().get('data', [])
        good = ['google', 'meta-llama', 'qwen', 'mistralai',
                'deepseek', 'anthropic', 'openai', 'microsoft']
        result = []
        for m in models:
            mid = m.get('id', '')
            pricing = m.get('pricing', {})
            try:
                pp = float(pricing.get('prompt', '1') or '1')
                cp = float(pricing.get('completion', '1') or '1')
            except (ValueError, TypeError):
                continue
            if pp != 0 or cp != 0:
                continue
            if mid.split('/')[0] in good:
                name = mid.lower()
                if 'instruct' in name or 'chat' in name or 'it:' in name:
                    result.append(mid)
        priority = {'google': 0, 'meta-llama': 1, 'qwen': 2,
                    'mistralai': 3, 'deepseek': 4, 'anthropic': 5,
                    'openai': 6, 'microsoft': 7}
        result.sort(key=lambda x: priority.get(x.split('/')[0], 99))
        return result[:4]
    except Exception:
        return []


class QuotesApp(ui.View):

    def __init__(self):
        self.name = 'مستخرج الاقتباسات الذكي'
        self.background_color = '#f8f9fa'
        self.flex = 'WH'
        self.pdf_path = None
        self.pdf_name = ''
        self._busy = False
        self.current_quotes = []
        self.current_header = ''
        self._last_build_width = 0
        self._cached_models = None
        self._last_success_model = None
        self._library = None
        self.quote_count = 10
        self.setup_ui()

    def _get_library(self):
        if self._library is None:
            self._library = load_library()
        return self._library

    def _save_library(self):
        if self._library is not None:
            save_library(self._library)

    def _find_quote(self, text, book):
        for q in self._get_library():
            if q['text'] == text and q['book'] == book:
                return q
        return None

    def _is_favorite(self, text, book):
        q = self._find_quote(text, book)
        return bool(q and q.get('favorite'))

    def _ensure_in_library(self, text, book):
        q = self._find_quote(text, book)
        if q is None:
            q = {
                'id': str(uuid.uuid4()),
                'text': text,
                'book': book,
                'favorite': False,
                'date': datetime.datetime.now().isoformat(),
            }
            self._get_library().append(q)
            self._save_library()
        return q

    def _set_favorite(self, text, book, value):
        q = self._ensure_in_library(text, book)
        q['favorite'] = bool(value)
        self._save_library()

    def layout(self):
        w = self.bounds.width
        h = self.bounds.height
        if w <= 0 or h < 400:
            return
        margin = 15
        content_w = w - (margin * 2)
        btn_w = (content_w - 10) / 2
        self.title_label.frame = (margin, 10, content_w, 30)
        self.api_field.frame = (margin, 45, content_w, 38)
        self.choose_button.frame = (margin, 90, btn_w, 40)
        self.generate_button.frame = (margin + btn_w + 10, 90, btn_w, 40)
        self.library_button.frame = (margin, 136, btn_w, 34)
        self.count_button.frame = (margin + btn_w + 10, 136, btn_w, 34)
        self.status_label.frame = (margin, 176, content_w, 30)
        self.activity_indicator.frame = ((w - 24) / 2, 182, 24, 24)
        self.scroll_view.frame = (margin, 214, content_w, max(120, h - 274))
        btn_y = h - 48
        self.copy_button.frame = (margin, btn_y, btn_w, 38)
        self.clear_button.frame = (margin + btn_w + 10, btn_y, btn_w, 38)
        last = getattr(self, '_last_build_width', 0)
        if self.current_quotes and abs(w - last) > 2:
            self._last_build_width = w
            self.build_quotes_view(self.current_quotes, self.current_header)

    def setup_ui(self):
        self.title_label = ui.Label()
        self.title_label.text = 'استخراج اقتباسات ذكي ✨'
        self.title_label.font = ('<System-Bold>', 17)
        self.title_label.alignment = ui.ALIGN_CENTER
        self.title_label.text_color = '#1a1a1a'
        self.add_subview(self.title_label)

        self.api_field = ui.TextField()
        self.api_field.placeholder = 'مفتاح OpenRouter (sk-or-v1-...)...'
        self.api_field.font = ('<System>', 14)
        self.api_field.border_width = 1
        self.api_field.border_color = '#cbd5e0'
        self.api_field.corner_radius = 8
        self.api_field.background_color = '#ffffff'
        self.api_field.text_color = '#000000'
        self.api_field.secure = True
        self.add_subview(self.api_field)
        saved = keychain.get_password('quotes_app', 'user_api_key')
        if saved:
            self.api_field.text = saved

        self.choose_button = self.create_button('📕 اختيار PDF', '#4a5568', self.choose_pdf)
        self.generate_button = self.create_button('استخراج ✨', '#2b6cb0', self.generate_quotes)
        self.library_button = self.create_button('📚 المكتبة', '#805ad5', self.open_library)
        self.count_button = self.create_button(
            '🔢 ' + str(self.quote_count) + ' اقتباسات', '#718096',
            self.change_count)

        self.status_label = ui.Label()
        self.status_label.text = 'ارفع ملف PDF للبدء'
        self.status_label.font = ('<System>', 12)
        self.status_label.text_color = '#718096'
        self.status_label.alignment = ui.ALIGN_CENTER
        self.status_label.number_of_lines = 2
        self.add_subview(self.status_label)

        self.activity_indicator = ui.ActivityIndicator()
        self.activity_indicator.style = ui.ACTIVITY_INDICATOR_STYLE_GRAY
        self.activity_indicator.hidden = True
        self.add_subview(self.activity_indicator)

        self.scroll_view = ui.ScrollView()
        self.scroll_view.background_color = '#f8f9fa'
        self.scroll_view.shows_vertical_scroll_indicator = True
        self.scroll_view.always_bounce_vertical = True
        self.add_subview(self.scroll_view)

        self.copy_button = self.create_button('نسخ الكل', '#2f855a', self.copy_results)
        self.clear_button = self.create_button('مسح الكل', '#c53030', self.clear_all)

    def create_button(self, title, color, action):
        b = ui.Button()
        b.title = title
        b.background_color = color
        b.tint_color = 'white'
        b.font = ('<System-Bold>', 13)
        b.corner_radius = 8
        b.action = action
        self.add_subview(b)
        return b

    def change_count(self, sender):
        options = [5, 10, 15]
        try:
            idx = options.index(self.quote_count)
        except ValueError:
            idx = 1
        self.quote_count = options[(idx + 1) % len(options)]
        sender.title = '🔢 ' + str(self.quote_count) + ' اقتباسات'

    def choose_pdf(self, sender):
        try:
            path = dialogs.pick_document(types=['com.adobe.pdf'])
            if not path:
                return
            file_name = os.path.basename(path)
            self.pdf_name = file_name
            try:
                docs = os.path.expanduser('~/Documents')
                if not os.path.exists(docs):
                    os.makedirs(docs)
                dest = os.path.join(docs, 'selected_book.pdf')
                if os.path.exists(dest):
                    try:
                        os.remove(dest)
                    except Exception:
                        pass
                shutil.copy(path, dest)
                self.pdf_path = dest
                self.status_label.text = '✓ ' + file_name
                self.status_label.text_color = '#2b6cb0'
                self.clear_quotes_view()
            except Exception:
                self.pdf_path = path
                self.status_label.text = '✓ ' + file_name + ' (مؤقت)'
                self.status_label.text_color = '#d69e2e'
                self.clear_quotes_view()
        except Exception as error:
            console.alert('خطأ', 'حدث خطأ:\n\n' + str(error), 'حسنًا')

    def clear_quotes_view(self):
        for sub in list(self.scroll_view.subviews):
            try:
                self.scroll_view.remove_subview(sub)
            except Exception:
                pass
        self.scroll_view.content_size = (0, 0)
        self.current_quotes = []
        self.current_header = ''

    def build_quotes_view(self, quotes, header_text):
        for sub in list(self.scroll_view.subviews):
            try:
                self.scroll_view.remove_subview(sub)
            except Exception:
                pass
        self.current_quotes = quotes
        self.current_header = header_text
        content_w = self.scroll_view.bounds.width - 20
        if content_w <= 0:
            content_w = 300
        y = 10
        if header_text:
            hh = estimate_label_height(header_text, content_w, 13)
            hl = ui.Label()
            hl.text = header_text
            hl.font = ('<System>', 13)
            hl.text_color = '#4a5568'
            hl.number_of_lines = 0
            hl.alignment = ui.ALIGN_RIGHT
            hl.frame = (10, y, content_w, hh)
            self.scroll_view.add_subview(hl)
            y += hh + 15
        for i, q in enumerate(quotes, 1):
            ch = self.create_quote_card(i, q, y, content_w)
            y += ch + 10
        self.scroll_view.content_size = (self.scroll_view.bounds.width, y + 20)

    def create_quote_card(self, idx, quote_text, y, width):
        card = ui.View()
        card.background_color = '#ffffff'
        card.border_width = 1
        card.border_color = '#e2e8f0'
        card.corner_radius = 10

        is_fav = self._is_favorite(quote_text, self.pdf_name)
        star = ui.Button()
        star.title = '★' if is_fav else '☆'
        star.font = ('<System>', 22)
        star.tint_color = '#d69e2e' if is_fav else '#a0aec0'
        star.frame = (8, 4, 36, 32)
        star.action = (lambda sender, t=quote_text:
                       self.toggle_star(sender, t))
        card.add_subview(star)

        num = ui.Label()
        num.text = '❝ اقتباس ' + str(idx)
        num.font = ('<System-Bold>', 14)
        num.text_color = '#2b6cb0'
        num.frame = (50, 10, width - 60, 22)
        num.alignment = ui.ALIGN_RIGHT
        card.add_subview(num)

        qh = estimate_label_height(quote_text, width, 14)
        ql = ui.Label()
        ql.text = quote_text
        ql.font = ('<System>', 14)
        ql.text_color = '#1a1a1a'
        ql.number_of_lines = 0
        ql.alignment = ui.ALIGN_RIGHT
        ql.frame = (10, 38, width - 20, qh)
        card.add_subview(ql)

        btn_y = 38 + qh + 8
        btn_h = 34
        gap = 6
        btn_w = (width - 20 - gap * 2) / 3

        copy_btn = self._small_btn('📋 نسخ', '#2f855a')
        copy_btn.frame = (width - btn_w - 10, btn_y, btn_w, btn_h)
        copy_btn.action = (lambda sender, t=quote_text:
                           self.copy_single_quote(t))
        card.add_subview(copy_btn)

        share_btn = self._small_btn('📤 مشاركة', '#2b6cb0')
        share_btn.frame = (width - (btn_w * 2) - 10 - gap, btn_y, btn_w, btn_h)
        share_btn.action = (lambda sender, t=quote_text:
                            self.share_single_quote(t))
        card.add_subview(share_btn)

        img_btn = self._small_btn('📸 صورة', '#805ad5')
        img_btn.frame = (width - (btn_w * 3) - 10 - gap * 2, btn_y, btn_w, btn_h)
        img_btn.action = (lambda sender, t=quote_text:
                          self.export_as_image(t))
        card.add_subview(img_btn)

        card_h = btn_y + btn_h + 10
        card.frame = (10, y, width, card_h)
        self.scroll_view.add_subview(card)
        return card_h

    def _small_btn(self, title, color):
        b = ui.Button()
        b.title = title
        b.font = ('<System-Bold>', 11)
        b.background_color = color
        b.tint_color = 'white'
        b.corner_radius = 6
        return b

    def toggle_star(self, sender, text):
        current = sender.title == '★'
        new_state = not current
        self._set_favorite(text, self.pdf_name, new_state)
        sender.title = '★' if new_state else '☆'
        sender.tint_color = '#d69e2e' if new_state else '#a0aec0'

    def copy_single_quote(self, text):
        clipboard.set(text)
        console.hud_alert('تم نسخ الاقتباس', 'success', 1.2)

    def share_single_quote(self, text):
        result = share_text_ios(text)
        if result is not True:
            console.hud_alert(str(result), 'error', 2)

    def export_as_image(self, text):
        def _work():
            footer = '📖 ' + self.pdf_name if self.pdf_name else ''
            path = render_quote_image(text, footer)
            if path.startswith('ERR'):
                def show_err():
                    console.alert('فشل توليد الصورة', path, 'حسنًا',
                                  hide_cancel_button=True)
                ui.in_background(show_err)()
                return

            def do_share():
                result = share_image_file(path)
                if result is not True:
                    console.hud_alert(str(result), 'error', 2)
            ui.in_background(do_share)()
        threading.Thread(target=_work).start()

    def ask_openrouter(self, api_key, book_text):
        seed = random.randint(1000, 9999)
        n = self.quote_count
        prompt = (
            'أنت مساعد متخصص في استخراج الاقتباسات الحرفية من الكتب العربية.\n\n'
            'تحذيرات مهمة جداً:\n'
            '- النص المرفق قد يحتوي على رموز غريبة أو أحرف صينية/يابانية أو unk.\n'
            '- تجاهل تماماً أي محتوى غير عربي.\n'
            '- اكتب بالعربية الفصحى فقط.\n\n'
            'رمز التنويع: ' + str(seed) + '\n\n'
            'المطلوب: استخرج ' + str(n) + ' اقتباسات قصيرة وعميقة ومختلفة.\n\n'
            'تعليمات الإخراج الصارمة:\n'
            '1. ابدأ القائمة مباشرة بالاقتباس الأول.\n'
            '2. عربي فقط.\n'
            '3. طابق النص حرفياً بدون اختلاق.\n'
            '4. رقّم من 1 إلى ' + str(n) + '.\n'
            '5. اذكر رقم الصفحة بصيغة [صفحة X].\n\n'
            'نص الكتاب:\n' + book_text)

        clean_key = api_key.strip()
        url = 'https://openrouter.ai/api/v1/chat/completions'
        headers = {
            'Authorization': 'Bearer ' + clean_key,
            'Content-Type': 'application/json',
            'HTTP-Referer': 'https://pythonista.app',
            'X-Title': 'Arabic Quotes Extractor',
        }

        if self._cached_models is None:
            @ui.in_background
            def show_loading():
                self.status_label.text = 'جلب النماذج...'
            show_loading()
            self._cached_models = get_free_arabic_models()

        models = []
        if self._last_success_model:
            models.append(self._last_success_model)
        for m in (self._cached_models or []):
            if m not in models:
                models.append(m)
        for fb in ['google/gemini-2.5-flash', 'openrouter/free']:
            if fb not in models:
                models.append(fb)

        all_errors = []
        for target in models:
            payload = {
                'model': target,
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.85,
                'max_tokens': 2500,
            }
            for attempt in range(2):
                @ui.in_background
                def upd(m=target, a=attempt + 1):
                    s = m.split('/')[-1].replace(':free', '')
                    if len(s) > 22:
                        s = s[:19] + '...'
                    self.status_label.text = '(' + s + ') محاولة ' + str(a) + '/2'
                upd()

                try:
                    r = requests.post(url, headers=headers,
                                      json=payload, timeout=90)
                except requests.exceptions.Timeout:
                    all_errors.append(target + ': مهلة')
                    time.sleep(2)
                    continue
                except requests.exceptions.ConnectionError:
                    raise Exception('تعذر الاتصال بالإنترنت.')
                except requests.exceptions.RequestException as e:
                    all_errors.append(target + ': ' + str(e)[:80])
                    time.sleep(2)
                    continue

                if r.status_code == 200:
                    res = r.json()
                    try:
                        answer = res['choices'][0]['message']['content'].strip()
                    except (KeyError, IndexError):
                        all_errors.append(target + ': استجابة فارغة')
                        continue
                    cleaned = clean_output_text(answer)
                    arabic = len(re.findall(r'[\u0600-\u06FF]', cleaned))
                    if arabic < 50:
                        all_errors.append(target + ': غير عربية')
                        continue
                    self._last_success_model = target
                    return cleaned, target

                try:
                    em = r.json().get('error', {}).get(
                        'message', 'كود ' + str(r.status_code))
                except Exception:
                    em = 'كود ' + str(r.status_code)
                all_errors.append(
                    target + ' (' + str(r.status_code) + '): ' + em[:100])

                if r.status_code in (429, 500, 503, 502):
                    time.sleep(5 * (2 ** attempt))
                    continue
                else:
                    break

        err_text = '\n'.join(all_errors[-6:]) if all_errors else 'لا تفاصيل'
        raise Exception('فشلت جميع المحاولات.\n\n' + err_text)

    def generate_quotes(self, sender):
        if self._busy:
            return
        api_key = self.api_field.text.strip()
        if not api_key:
            console.alert('مفتاح API مفقود', 'يرجى إدخال مفتاح OpenRouter.', 'حسنًا')
            return
        if not self.pdf_path:
            console.alert('لم يتم اختيار ملف', 'اضغط "اختيار PDF" أولاً.', 'حسنًا')
            return

        keychain.set_password('quotes_app', 'user_api_key', api_key)

        self._busy = True
        self.generate_button.enabled = False
        self.choose_button.enabled = False
        self.library_button.enabled = False
        self.count_button.enabled = False
        self.activity_indicator.hidden = False
        self.activity_indicator.start()

        if self.current_quotes:
            self.status_label.text = 'انتظار 5 ثوانٍ (توفير الحصة)...'
            self.status_label.text_color = '#d69e2e'
        else:
            self.status_label.text = 'جاري قراءة PDF...'
            self.status_label.text_color = '#718096'

        def worker():
            try:
                if self.current_quotes:
                    time.sleep(5)
                book_text = extract_random_text_from_pdf(self.pdf_path, 30)
                if not book_text.strip():
                    def empty():
                        self.status_label.text = 'لا يوجد نص في الملف'
                        self.status_label.text_color = '#c53030'
                        self.show_message_card('لم يُعثر على نص داخل PDF.')
                        self.reset_loading()
                    ui.in_background(empty)()
                    return
                book_text = clean_pdf_text_for_prompt(book_text)
                book_text = truncate_at_line(book_text, 12000)
                if not book_text.strip():
                    def empty2():
                        self.status_label.text = 'نص PDF غير صالح'
                        self.status_label.text_color = '#c53030'
                        self.show_message_card('لم يتبقّ محتوى عربي.')
                        self.reset_loading()
                    ui.in_background(empty2)()
                    return

                answer, used_model = self.ask_openrouter(api_key, book_text)
                short = used_model.split('/')[-1].replace(':free', '')
                if len(short) > 30:
                    short = short[:27] + '...'
                header = '📕 ' + self.pdf_name + '\n🤖 ' + short
                quotes = parse_quotes(answer)

                for q in quotes:
                    self._ensure_in_library(q, self.pdf_name)
                self._save_library()

                def on_success():
                    try:
                        self.build_quotes_view(quotes, header)
                        self.status_label.text = ('✓ ' + str(len(quotes))
                                                  + ' اقتباسات — اضغط للمزيد')
                        self.status_label.text_color = '#2f855a'
                        self.generate_button.title = '🔄 اقتباسات جديدة'
                    except Exception as ui_err:
                        self.status_label.text = 'خطأ العرض: ' + str(ui_err)
                        self.status_label.text_color = '#c53030'
                    finally:
                        self.reset_loading()
                ui.in_background(on_success)()
            except Exception as e:
                msg = str(e)
                def on_error():
                    try:
                        self.show_message_card('حدث خطأ:\n\n' + msg, '#c53030')
                        self.status_label.text = 'حدث خطأ!'
                        self.status_label.text_color = '#c53030'
                    finally:
                        self.reset_loading()
                ui.in_background(on_error)()

        threading.Thread(target=worker).start()

    def show_message_card(self, message, color='#4a5568'):
        for sub in list(self.scroll_view.subviews):
            try:
                self.scroll_view.remove_subview(sub)
            except Exception:
                pass
        cw = self.scroll_view.bounds.width - 20
        if cw <= 0:
            cw = 300
        h = estimate_label_height(message, cw, 14)
        l = ui.Label()
        l.text = message
        l.font = ('<System>', 14)
        l.text_color = color
        l.number_of_lines = 0
        l.alignment = ui.ALIGN_CENTER
        l.frame = (10, 20, cw, h)
        self.scroll_view.add_subview(l)
        self.scroll_view.content_size = (self.scroll_view.bounds.width, h + 40)

    def reset_loading(self):
        self._busy = False
        self.generate_button.enabled = True
        self.choose_button.enabled = True
        self.library_button.enabled = True
        self.count_button.enabled = True
        self.activity_indicator.stop()
        self.activity_indicator.hidden = True

    def copy_results(self, sender):
        if not self.current_quotes:
            console.hud_alert('لا توجد نتائج', 'error', 1.5)
            return
        parts = [self.current_header, '']
        for i, q in enumerate(self.current_quotes, 1):
            parts.append(str(i) + '. ' + q)
            parts.append('')
        clipboard.set('\n'.join(parts).strip())
        console.hud_alert('تم نسخ جميع الاقتباسات', 'success', 1.5)

    def clear_all(self, sender):
        self.pdf_path = None
        self.pdf_name = ''
        self.status_label.text = 'ارفع ملف PDF للبدء'
        self.status_label.text_color = '#718096'
        self.generate_button.title = 'استخراج ✨'
        self.clear_quotes_view()

    def open_library(self, sender):
        lib = LibraryView(self)
        lib.present('sheet', title_bar_color='#805ad5')


class LibraryView(ui.View):

    def __init__(self, parent):
        self.parent = parent
        self.name = '📚 مكتبة الاقتباسات'
        self.background_color = '#f8f9fa'
        self.filter_mode = 'all'
        self._last_w = 0
        self.setup_ui()

    def setup_ui(self):
        self.close_btn = ui.Button(title='إغلاق')
        self.close_btn.font = ('<System-Bold>', 14)
        self.close_btn.tint_color = '#2b6cb0'
        self.close_btn.action = self._on_close
        self.add_subview(self.close_btn)

        self.filter_btn = ui.Button(title='')
        self.filter_btn.font = ('<System-Bold>', 13)
        self.filter_btn.background_color = '#805ad5'
        self.filter_btn.tint_color = 'white'
        self.filter_btn.corner_radius = 8
        self.filter_btn.action = self.toggle_filter
        self.add_subview(self.filter_btn)

        self.count_label = ui.Label()
        self.count_label.font = ('<System>', 12)
        self.count_label.text_color = '#718096'
        self.count_label.alignment = ui.ALIGN_CENTER
        self.add_subview(self.count_label)

        self.scroll = ui.ScrollView()
        self.scroll.background_color = '#f8f9fa'
        self.scroll.shows_vertical_scroll_indicator = True
        self.add_subview(self.scroll)

    def layout(self):
        w = self.bounds.width
        h = self.bounds.height
        if w <= 0:
            return
        margin = 15
        self.close_btn.frame = (w - 90, 8, 80, 30)
        self.filter_btn.frame = (margin, 46, (w - margin * 2) / 2 - 5, 36)
        self.count_label.frame = (w / 2 + 5, 46, (w - margin * 2) / 2 - 5, 36)
        self.scroll.frame = (margin, 92, w - margin * 2, h - 107)
        if abs(w - self._last_w) > 2:
            self._last_w = w
            self.rebuild()

    def _on_close(self, sender):
        try:
            self.close()
        except Exception:
            pass

    def toggle_filter(self, sender):
        self.filter_mode = 'favorites' if self.filter_mode == 'all' else 'all'
        self.rebuild()

    def rebuild(self):
        lib = self.parent._get_library()
        if self.filter_mode == 'favorites':
            items = [q for q in lib if q.get('favorite')]
        else:
            items = list(lib)
        items = sorted(items, key=lambda x: x.get('date', ''), reverse=True)

        self.filter_btn.title = ('الكل' if self.filter_mode == 'all'
                                 else '⭐ المفضلة')
        self.count_label.text = str(len(items)) + ' اقتباس'

        for sub in list(self.scroll.subviews):
            try:
                self.scroll.remove_subview(sub)
            except Exception:
                pass

        real_w = self.scroll.bounds.width
        if real_w < 50:
            real_w = self.bounds.width - 30
        if real_w < 50:
            real_w = 350

        if not items:
            l = ui.Label()
            l.text = 'لا توجد اقتباسات بعد.'
            l.font = ('<System>', 14)
            l.text_color = '#a0aec0'
            l.number_of_lines = 0
            l.alignment = ui.ALIGN_CENTER
            l.frame = (10, 40, real_w - 20, 100)
            self.scroll.add_subview(l)
            self.scroll.content_size = (real_w, 200)
            return

        y = 10
        w = real_w - 20
        for q in items:
            h_card = self.create_lib_card(q, y, w)
            y += h_card + 10
        self.scroll.content_size = (real_w, y + 20)

    def create_lib_card(self, q, y, width):
        card = ui.View()
        card.background_color = '#ffffff'
        card.border_width = 1
        card.border_color = '#e2e8f0'
        card.corner_radius = 10

        is_fav = q.get('favorite', False)
        star = ui.Button()
        star.title = '★' if is_fav else '☆'
        star.font = ('<System>', 22)
        star.tint_color = '#d69e2e' if is_fav else '#a0aec0'
        star.frame = (8, 4, 36, 32)
        star.action = (lambda sender, qq=q, sb=star: self.toggle_star(qq, sb))
        card.add_subview(star)

        info = ui.Label()
        book = q.get('book', '')
        date_str = q.get('date', '')[:10]
        info.text = '📕 ' + book + '  ·  ' + date_str
        info.font = ('<System>', 11)
        info.text_color = '#4a5568'
        info.frame = (50, 8, width - 60, 20)
        info.alignment = ui.ALIGN_RIGHT
        card.add_subview(info)

        text = q.get('text', '')
        qh = estimate_label_height(text, width, 14)
        ql = ui.Label()
        ql.text = text
        ql.font = ('<System>', 14)
        ql.text_color = '#1a1a1a'
        ql.number_of_lines = 0
        ql.alignment = ui.ALIGN_RIGHT
        ql.frame = (10, 34, width - 20, qh)
        card.add_subview(ql)

        btn_y = 34 + qh + 8
        btn_h = 32
        gap = 6
        btn_w = (width - 20 - gap * 2) / 3

        copy_b = self.small_btn('📋 نسخ', '#2f855a')
        copy_b.frame = (width - btn_w - 10, btn_y, btn_w, btn_h)
        copy_b.action = (lambda sender, t=text: self.do_copy(t))
        card.add_subview(copy_b)

        share_b = self.small_btn('📤 مشاركة', '#2b6cb0')
        share_b.frame = (width - (btn_w * 2) - 10 - gap, btn_y, btn_w, btn_h)
        share_b.action = (lambda sender, t=text: self.do_share(t))
        card.add_subview(share_b)

        del_b = self.small_btn('🗑 حذف', '#c53030')
        del_b.frame = (width - (btn_w * 3) - 10 - gap * 2, btn_y, btn_w, btn_h)
        del_b.action = (lambda sender, qq=q: self.do_delete(qq))
        card.add_subview(del_b)

        card_h = btn_y + btn_h + 10
        card.frame = (10, y, width, card_h)
        self.scroll.add_subview(card)
        return card_h

    def small_btn(self, title, color):
        b = ui.Button()
        b.title = title
        b.font = ('<System-Bold>', 11)
        b.background_color = color
        b.tint_color = 'white'
        b.corner_radius = 6
        return b

    def toggle_star(self, q, sender):
        new_val = not q.get('favorite', False)
        q['favorite'] = new_val
        self.parent._save_library()
        sender.title = '★' if new_val else '☆'
        sender.tint_color = '#d69e2e' if new_val else '#a0aec0'

    def do_copy(self, text):
        clipboard.set(text)
        console.hud_alert('تم النسخ', 'success', 1.2)

    def do_share(self, text):
        result = share_text_ios(text)
        if result is not True:
            console.hud_alert(str(result), 'error', 2)

    def do_delete(self, q):
        lib = self.parent._get_library()
        lib = [x for x in lib if x.get('id') != q.get('id')]
        self.parent._library = lib
        self.parent._save_library()
        self.rebuild()


if __name__ == '__main__':
    app = QuotesApp()
    app.present('sheet')
