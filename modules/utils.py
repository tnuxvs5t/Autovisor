import ctypes
import json
import os.path
import traceback
from typing import List
from playwright.async_api import Page, Locator
from playwright.async_api import TimeoutError
from pygetwindow import Win32Window

from modules.configs import Config
import time
import pygetwindow as gw
from modules.logger import Logger

logger = Logger()

CARD_SCAN_JS = r"""
() => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const progressRe = /学习进度\s*([0-9]{1,3})%/;
    const badTitle = /^(学习进度|掌握度|知识模块|知识单元|\d+%?)$/;
    const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width >= 180 &&
            rect.height >= 120 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const titleFrom = (el) => {
        const walkers = el.querySelectorAll('[title], h1, h2, h3, h4, h5, h6, strong, b, p, span, div');
        for (const node of walkers) {
            const text = normalize(node.getAttribute && node.getAttribute('title') ? node.getAttribute('title') : node.textContent);
            if (!text || text.length > 80 || badTitle.test(text) || text.includes('学习进度') || text.includes('掌握度')) {
                continue;
            }
            return text;
        }
        const lines = normalize(el.innerText).split(/(?<=\S)\s{2,}|\n/).map((x) => normalize(x)).filter(Boolean);
        for (const line of lines) {
            if (!badTitle.test(line) && !line.includes('学习进度') && !line.includes('掌握度') && line.length <= 80) {
                return line;
            }
        }
        return '';
    };
    const sectionFrom = (el) => {
        let parent = el.parentElement;
        while (parent) {
            const text = normalize(parent.innerText || '');
            const lines = text.split(/\n/).map((x) => normalize(x)).filter(Boolean);
            for (const line of lines.slice(0, 6)) {
                if (line && line.length <= 40 && !line.includes('学习进度') && !line.includes('掌握度') && !line.includes('%')) {
                    if (line.includes('知识模块') || line.includes('知识单元')) {
                        continue;
                    }
                    return line;
                }
            }
            parent = parent.parentElement;
        }
        return '';
    };
    const all = Array.from(document.querySelectorAll('div, article, section, li, a, button'));
    const raw = [];
    for (const el of all) {
        if (!visible(el)) continue;
        const text = normalize(el.innerText || '');
        const match = text.match(progressRe);
        if (!match) continue;
        if ((text.match(/学习进度/g) || []).length !== 1) continue;
        const title = titleFrom(el);
        if (!title) continue;
        const rect = el.getBoundingClientRect();
        raw.push({
            element: el,
            title,
            section: sectionFrom(el),
            progress: Math.max(0, Math.min(100, Number(match[1]))),
            top: rect.top + window.scrollY,
            left: rect.left + window.scrollX,
            area: rect.width * rect.height,
        });
    }
    const chosen = new Map();
    for (const item of raw) {
        const key = `${item.section}@@${item.title}`;
        const prev = chosen.get(key);
        if (!prev || item.area < prev.area) {
            chosen.set(key, item);
        }
    }
    if (!window.__autovisorCardSeq) {
        window.__autovisorCardSeq = 1;
    }
    const cards = [];
    for (const [key, item] of chosen.entries()) {
        if (!item.element.dataset.autovisorCardId) {
            item.element.dataset.autovisorCardId = `autovisor-${window.__autovisorCardSeq++}`;
        }
        cards.push({
            key,
            card_id: item.element.dataset.autovisorCardId,
            title: item.title,
            section: item.section,
            progress: item.progress,
            top: item.top,
            left: item.left,
        });
    }
    cards.sort((a, b) => a.top - b.top || a.left - b.left);
    return {
        cards,
        scrollTop: window.scrollY,
        viewportHeight: window.innerHeight,
        scrollHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
    };
}
"""

SCROLL_ROOTS_JS = r"""
() => {
    const roots = [{scope_id: 'window', kind: 'window', label: 'window'}];
    let seq = 1;
    for (const el of Array.from(document.querySelectorAll('*'))) {
        const style = window.getComputedStyle(el);
        const overflowY = style.overflowY;
        const scrollable = (overflowY === 'auto' || overflowY === 'scroll') &&
            el.scrollHeight > el.clientHeight + 160 &&
            el.clientHeight > 220 &&
            el.clientWidth > 320;
        if (!scrollable) continue;
        if (!el.dataset.autovisorScrollId) {
            el.dataset.autovisorScrollId = `autovisor-scroll-${seq++}`;
        }
        roots.push({
            scope_id: el.dataset.autovisorScrollId,
            kind: 'element',
            label: el.className || el.id || el.tagName.toLowerCase(),
        });
    }
    return roots;
}
"""

CARD_SCAN_SCOPE_JS = r"""
(scopeId) => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const progressRe = /学习进度\s*([0-9]{1,3})%/;
    const badTitle = /^(学习进度|掌握度|知识模块|知识单元|\d+%?)$/;
    const root = scopeId === 'window'
        ? window
        : document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
    if (!root) {
        return { cards: [], scrollTop: 0, viewportHeight: 0, scrollHeight: 0 };
    }
    const rootRect = scopeId === 'window'
        ? { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth }
        : root.getBoundingClientRect();
    const visibleInScope = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const intersects = rect.bottom > rootRect.top + 8 &&
            rect.top < rootRect.bottom - 8 &&
            rect.right > rootRect.left + 8 &&
            rect.left < rootRect.right - 8;
        return rect.width >= 180 &&
            rect.height >= 120 &&
            intersects &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };
    const titleFrom = (el) => {
        const walkers = el.querySelectorAll('[title], h1, h2, h3, h4, h5, h6, strong, b, p, span, div');
        for (const node of walkers) {
            const text = normalize(node.getAttribute && node.getAttribute('title') ? node.getAttribute('title') : node.textContent);
            if (!text || text.length > 80 || badTitle.test(text) || text.includes('学习进度') || text.includes('掌握度')) {
                continue;
            }
            return text;
        }
        const lines = normalize(el.innerText).split(/(?<=\S)\s{2,}|\n/).map((x) => normalize(x)).filter(Boolean);
        for (const line of lines) {
            if (!badTitle.test(line) && !line.includes('学习进度') && !line.includes('掌握度') && line.length <= 80) {
                return line;
            }
        }
        return '';
    };
    const sectionFrom = (el) => {
        let parent = el.parentElement;
        while (parent) {
            const text = normalize(parent.innerText || '');
            const lines = text.split(/\n/).map((x) => normalize(x)).filter(Boolean);
            for (const line of lines.slice(0, 6)) {
                if (line && line.length <= 40 && !line.includes('学习进度') && !line.includes('掌握度') && !line.includes('%')) {
                    if (line.includes('知识模块') || line.includes('知识单元')) continue;
                    return line;
                }
            }
            parent = parent.parentElement;
        }
        return '';
    };
    const source = scopeId === 'window' ? document : root;
    const raw = [];
    for (const el of Array.from(source.querySelectorAll('div, article, section, li, a, button'))) {
        if (!visibleInScope(el)) continue;
        const text = normalize(el.innerText || '');
        const match = text.match(progressRe);
        if (!match) continue;
        if ((text.match(/学习进度/g) || []).length !== 1) continue;
        const title = titleFrom(el);
        if (!title) continue;
        const rect = el.getBoundingClientRect();
        raw.push({
            element: el,
            title,
            section: sectionFrom(el),
            progress: Math.max(0, Math.min(100, Number(match[1]))),
            top: scopeId === 'window' ? rect.top + window.scrollY : rect.top - rootRect.top + root.scrollTop,
            left: scopeId === 'window' ? rect.left + window.scrollX : rect.left - rootRect.left + root.scrollLeft,
            area: rect.width * rect.height,
        });
    }
    const chosen = new Map();
    for (const item of raw) {
        const key = `${scopeId}@@${item.section}@@${item.title}`;
        const prev = chosen.get(key);
        if (!prev || item.area < prev.area) {
            chosen.set(key, item);
        }
    }
    if (!window.__autovisorCardSeq) {
        window.__autovisorCardSeq = 1;
    }
    const cards = [];
    for (const [key, item] of chosen.entries()) {
        if (!item.element.dataset.autovisorCardId) {
            item.element.dataset.autovisorCardId = `autovisor-${window.__autovisorCardSeq++}`;
        }
        cards.push({
            key,
            scope_id: scopeId,
            card_id: item.element.dataset.autovisorCardId,
            title: item.title,
            section: item.section,
            progress: item.progress,
            top: item.top,
            left: item.left,
        });
    }
    cards.sort((a, b) => a.top - b.top || a.left - b.left);
    return {
        cards,
        scrollTop: scopeId === 'window' ? window.scrollY : root.scrollTop,
        viewportHeight: scopeId === 'window' ? window.innerHeight : root.clientHeight,
        scrollHeight: scopeId === 'window'
            ? Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
            : root.scrollHeight,
    };
}
"""

def save_cookies(cookies, filename="cookies.json"):
    """保存登录Cookies到文件"""
    with open(filename, 'w') as f:
        json.dump(cookies, f)

def load_cookies(filename="cookies.json"):
    """从文件加载 Cookies"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None

# 将python终端前置
def bring_console_to_front():
    # 获取当前控制台窗口句柄
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
        ctypes.windll.user32.SetForegroundWindow(hwnd)


async def display_window(page: Page) -> None:
    window = await get_browser_window(page)
    if window:
        window.moveTo(100, 100)
        logger.info("播放窗口已自动前置.", shift=True)
    else:
        logger.warn("未找到播放窗口!")


async def hide_window(page: Page) -> None:
    window = await get_browser_window(page)
    if window:
        window.moveTo(-3200, -3200)
        logger.info("播放窗口已自动隐藏.")
    else:
        logger.warn("未找到播放窗口!")


async def get_browser_window(page: Page) -> Win32Window | None:
    custom_title = "Autovisor - Playwright"
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(f'document.title = "{custom_title}"')
    # 获取所有窗口并尝试匹配 Playwright 窗口
    await page.wait_for_timeout(1000)
    win_list = gw.getWindowsWithTitle(custom_title)
    if win_list:
        return win_list[0]
    else:
        return None


async def evaluate_js(page: Page, wait_selector, js: str, timeout=None, is_hike_class=False) -> None:
    try:
        if wait_selector and is_hike_class is False:
            wait_timeout = 1200 if timeout is None else timeout
            await page.wait_for_selector(wait_selector, timeout=wait_timeout)
        if is_hike_class is False:
            await page.evaluate(js)
    except TimeoutError:
        return
    except Exception as e:
        logger.write_log(f"Exec JS failed: {js} Selector:{wait_selector} Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return


async def evaluate_on_element(page: Page, selector: str, js: str, timeout: float = None,
                              is_hike_class=False) -> None:
    try:
        if selector and is_hike_class is False:
            element = page.locator(selector).first
            eval_timeout = 1200 if timeout is None else timeout
            await element.evaluate(js, timeout=eval_timeout)
    except TimeoutError:
        return
    except Exception as e:
        logger.write_log(f"Exec JS failed: Selector:{selector} JS:{js} Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return


async def optimize_page(page: Page, config: Config, is_new_version=False, is_hike_class=False) -> None:
    try:
        #await page.wait_for_load_state("domcontentloaded")
        await evaluate_js(page, ".studytime-div", config.pop_js, None, is_hike_class)
        if not is_new_version:
            if not is_hike_class:
                hour = time.localtime().tm_hour
                if hour >= 18 or hour < 7:
                    await evaluate_on_element(page, ".Patternbtn-div", "el=>el.click()", timeout=1500)
                await evaluate_on_element(page, ".exploreTip", "el=>el.remove()", timeout=1500)
                await evaluate_on_element(page, ".ai-helper-Index2", "el=>el.remove()", timeout=1500)
                await evaluate_on_element(page, ".aiMsg.once", "el=>el.remove()", timeout=1500)
                logger.info("页面优化完成!")

    except Exception as e:
        logger.write_log(f"Exec optimize_page failed. Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return


async def get_video_attr(page, attr: str) -> any:
    try:
        await page.wait_for_selector("video", state="attached", timeout=1000)
        attr = await page.evaluate(f'''document.querySelector('video').{attr}''')
        return attr
    except Exception as e:
        logger.write_log(f"Exec get_video_attr failed. Error:{repr(e)}\n")
        logger.write_log(traceback.format_exc())
        return None


async def get_lesson_name(page: Page, is_hike_class=False) -> str:
    if is_hike_class:
        #title_ele1 = await page.wait_for_selector("#sourceTit")
        title_ele = await page.wait_for_selector("span")
        await page.wait_for_timeout(500)
        title = await title_ele.get_attribute("title")
    else:
        title_ele = await page.wait_for_selector("#lessonOrder")
        await page.wait_for_timeout(500)
        title = await title_ele.get_attribute("title")
    return title


def parse_progress_text(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return max(0, min(int(digits), 100))
    except ValueError:
        return None


async def get_lesson_cards(page: Page, is_hike_class=False) -> List[Locator]:
    if is_hike_class:
        return await page.locator(".file-item").all()
    return await page.locator(".clearfix.video").all()


async def get_card_progress(card: Locator, is_new_version=False, is_hike_class=False) -> tuple[int, bool]:
    if is_hike_class:
        progress_text = None
        rate_count = await card.locator(".rate").count()
        if rate_count:
            progress_text = await card.locator(".rate").first.text_content()
        progress = parse_progress_text(progress_text)
        finished = await card.locator(".icon-finish").count() > 0 or progress == 100
        return (100 if finished else progress or 0), finished

    progress_text = None
    progress_count = await card.locator(".progress-num").count()
    if progress_count:
        progress_text = await card.locator(".progress-num").first.text_content()
    progress = parse_progress_text(progress_text)
    finished = await card.locator(".time_icofinish").count() > 0 or progress == 100
    if is_new_version and progress is not None:
        finished = progress == 100
    return (100 if finished else progress or 0), finished


async def get_card_title(card: Locator) -> str:
    title_selectors = (
        "#lessonOrder",
        ".file-name",
        ".title",
        ".name",
        "[title]",
        "span",
    )
    for selector in title_selectors:
        candidates = card.locator(selector)
        if await candidates.count():
            locator = candidates.first
            title_attr = await locator.get_attribute("title")
            if title_attr and title_attr.strip():
                return title_attr.strip()
            text = await locator.text_content()
            if text and text.strip():
                return text.strip()
    text = await card.text_content()
    return text.strip() if text else "未命名节次"


async def scan_pending_lessons(page: Page, is_new_version=False, is_hike_class=False) -> list[dict]:
    try:
        if is_new_version:
            await page.wait_for_selector(".progress-num", timeout=2000)
        if is_hike_class:
            await page.wait_for_selector(".icon-finish", timeout=2000)
        else:
            await page.wait_for_selector(".time_icofinish", timeout=2000)
    except TimeoutError:
        pass

    pending_lessons = []
    all_cards = await get_lesson_cards(page, is_hike_class)
    for index, card in enumerate(all_cards):
        progress, finished = await get_card_progress(card, is_new_version, is_hike_class)
        if finished:
            continue
        title = await get_card_title(card)
        pending_lessons.append(
            {
                "index": index,
                "title": title,
                "progress": progress,
            }
        )
    pending_lessons.sort(key=lambda item: (item["progress"], item["index"]))
    logger.write_log(f"Scanned pending lessons: {len(pending_lessons)}\n")
    return pending_lessons


def summarize_cards(cards: list[dict]) -> dict:
    summary = {
        "total": len(cards),
        "pending": 0,
        "done": 0,
        "sections": {},
    }
    for card in cards:
        done = card["progress"] >= 100
        if done:
            summary["done"] += 1
        else:
            summary["pending"] += 1
        section = card.get("section") or "未分组"
        section_state = summary["sections"].setdefault(section, {"total": 0, "pending": 0, "done": 0})
        section_state["total"] += 1
        if done:
            section_state["done"] += 1
        else:
            section_state["pending"] += 1
    return summary


async def collect_scrollable_cards(page: Page, max_rounds: int = 30) -> list[dict]:
    roots = await page.evaluate(SCROLL_ROOTS_JS)
    logger.info(f"检测到滚动容器: {len(roots)} 个")
    merged: dict[str, dict] = {}

    for root in roots:
        scope_id = root["scope_id"]
        logger.info(f"开始扫描容器: {root['label']} ({scope_id})")
        if scope_id == "window":
            await page.evaluate("window.scrollTo(0, 0)")
        else:
            await page.evaluate(
                """(scopeId) => {
                    const el = document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
                    if (el) el.scrollTop = 0;
                }""",
                scope_id,
            )
        await page.wait_for_timeout(400)

        last_signature = None
        stable_rounds = 0
        for _ in range(max_rounds):
            result = await page.evaluate(CARD_SCAN_SCOPE_JS, scope_id)
            cards = result["cards"]
            for card in cards:
                key = card["key"]
                prev = merged.get(key)
                if not prev or card["progress"] != prev["progress"] or card["top"] < prev["top"]:
                    merged[key] = card

            signature = (scope_id, len(cards), result["scrollTop"], result["scrollHeight"])
            if signature == last_signature:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_signature = signature

            bottom_reached = result["scrollTop"] + result["viewportHeight"] >= result["scrollHeight"] - 8
            if bottom_reached and stable_rounds >= 1:
                break

            step = max(240, int(result["viewportHeight"] * 0.85))
            if scope_id == "window":
                await page.evaluate("(step) => window.scrollBy(0, step)", step)
            else:
                await page.evaluate(
                    """([scopeId, step]) => {
                        const el = document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
                        if (el) el.scrollBy(0, step);
                    }""",
                    [scope_id, step],
                )
            await page.wait_for_timeout(650)
        logger.info(f"容器扫描完成: {root['label']} | 当前累计卡片 {len(merged)}")

    await page.evaluate("window.scrollTo(0, 0)")
    for root in roots:
        scope_id = root["scope_id"]
        if scope_id == "window":
            continue
        await page.evaluate(
            """(scopeId) => {
                const el = document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
                if (el) el.scrollTop = 0;
            }""",
            scope_id,
        )
    await page.wait_for_timeout(300)
    cards = list(merged.values())
    cards.sort(key=lambda item: (item["progress"], item["top"], item["left"]))
    return cards


async def scan_pending_lessons_deep(page: Page) -> tuple[list[dict], dict]:
    cards = await collect_scrollable_cards(page)
    summary = summarize_cards(cards)
    pending = [card for card in cards if card["progress"] < 100]
    return pending, summary


async def click_card_by_id(
    page: Page,
    card_id: str,
    title: str | None = None,
    scope_id: str | None = None,
    top: int | None = None,
) -> bool:
    selector = f'[data-autovisor-card-id="{card_id}"]'
    try:
        if scope_id and top is not None:
            if scope_id == "window":
                await page.evaluate("(top) => window.scrollTo(0, Math.max(top - 120, 0))", top)
            else:
                await page.evaluate(
                    """([scopeId, top]) => {
                        const el = document.querySelector(`[data-autovisor-scroll-id="${scopeId}"]`);
                        if (el) el.scrollTop = Math.max(top - 80, 0);
                    }""",
                    [scope_id, top],
                )
            await page.wait_for_timeout(400)
        locator = page.locator(selector).first
        if await locator.count():
            await locator.scroll_into_view_if_needed()
            await page.wait_for_timeout(300)
            await locator.click(timeout=2000)
            return True
    except Exception:
        pass

    if title:
        try:
            title_locator = page.get_by_text(title, exact=True).first
            if await title_locator.count():
                await title_locator.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await title_locator.click(timeout=2000)
                return True
        except Exception:
            pass
    return False


async def mark_lesson(card: Locator, token: str | None = None) -> None:
    if token:
        await card.evaluate(
            """(el, token) => {
                el.dataset.autovisorMark = token;
                el.style.outline = '2px solid #2563eb';
                el.style.outlineOffset = '2px';
            }""",
            token,
        )
        return
    await card.evaluate(
        """(el) => {
            delete el.dataset.autovisorMark;
            el.style.outline = '';
            el.style.outlineOffset = '';
        }"""
    )


async def leave_lesson_view(page: Page) -> None:
    selectors = (
        ".back-btn",
        ".btn-back",
        ".course-back",
        ".icon-return",
        ".icon-back",
        ".back",
    )
    for selector in selectors:
        try:
            candidates = page.locator(selector)
            if await candidates.count():
                locator = candidates.first
                if not await locator.is_visible():
                    continue
                await locator.click(timeout=1000)
                await page.wait_for_timeout(800)
                return
        except Exception:
            continue
