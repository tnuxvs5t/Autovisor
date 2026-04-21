# encoding=utf-8
import asyncio
import sys
import time
import traceback
from dataclasses import dataclass, field

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Browser, BrowserContext, Page, Playwright, TimeoutError, async_playwright

from modules.configs import Config
from modules.logger import Logger
from modules.progress import show_course_progress
from modules.support import show_donate
from modules.tasks import activate_window, play_video, skip_questions, status_ocr_stream, task_monitor, video_optimize, wait_for_verify
from modules.utils import (
    click_card_by_id,
    get_browser_window,
    get_lesson_name,
    get_video_attr,
    hide_window,
    load_cookies,
    optimize_page,
    save_cookies,
    scan_pending_lessons_deep,
)

logger = Logger()
config: Config


@dataclass
class WorkerSession:
    worker_id: int
    context: BrowserContext
    page: Page
    verify_event: asyncio.Event = field(default_factory=asyncio.Event)
    answer_event: asyncio.Event = field(default_factory=asyncio.Event)
    restart_event: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: list[asyncio.Task] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"W{self.worker_id}"


class WorkerRestart(Exception):
    pass


class ClaimManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._claimed: set[str] = set()

    @staticmethod
    def make_key(course_url: str, lesson: dict) -> str:
        return f"{course_url}|{lesson.get('scope_id', 'window')}|{lesson.get('card_id', '')}|{lesson.get('title', '')}"

    async def claim(self, course_url: str, lesson: dict) -> str | None:
        key = self.make_key(course_url, lesson)
        async with self._lock:
            if key in self._claimed:
                return None
            self._claimed.add(key)
            return key

    async def release(self, claim_key: str | None) -> None:
        if not claim_key:
            return
        async with self._lock:
            self._claimed.discard(claim_key)


async def launch_browser(p: Playwright) -> tuple[Browser, str]:
    driver = "msedge" if config.driver == "edge" else config.driver
    logger.info(f"正在启动{config.driver}浏览器...")
    browser = await p.chromium.launch(
        channel=driver,
        headless=False,
        executable_path=config.exe_path if config.exe_path else None,
        args=[
            "--window-size=1600,900",
            "--window-position=100,100",
        ],
    )
    with open("res/stealth.min.js", "r", encoding="utf-8") as f:
        stealth_js = f.read()
    return browser, stealth_js


async def create_context(browser: Browser, stealth_js: str, cookies: list | None = None) -> BrowserContext:
    context = await browser.new_context()
    await context.add_init_script(stealth_js)
    if cookies:
        await context.add_cookies(cookies)
    return context


async def bootstrap_login(browser: Browser, stealth_js: str) -> list:
    bootstrap_context = await create_context(browser, stealth_js, load_cookies("res/cookies.json"))
    page = await bootstrap_context.new_page()
    page.set_default_timeout(24 * 3600 * 1000)

    async def request_handler(request):
        if "https://www.zhihuishu.com" in request.url:
            cookies = await bootstrap_context.cookies()
            save_cookies(cookies, "res/cookies.json")
            logger.info("已保存登录凭证到: res/cookies.json,下次可免密登录.")
            page.remove_listener("request", request_handler)

    await page.goto(config.login_url, wait_until="commit")
    if "login" in page.url:
        logger.info("正在等待登录完成...")
        page.on("request", request_handler)
        if config.username and config.password:
            await page.wait_for_selector("#lUsername", state="attached")
            await page.wait_for_selector("#lPassword", state="attached")
            await page.locator("#lUsername").fill(config.username)
            await page.locator("#lPassword").fill(config.password)
            await page.wait_for_selector(".wall-sub-btn", state="attached")
            await page.wait_for_timeout(500)
            await page.locator(".wall-sub-btn").first.click()
        await page.wait_for_selector(".wall-main", state="hidden")
    else:
        logger.info("检测到已登录,跳过登录步骤.")

    cookies = await bootstrap_context.cookies()
    save_cookies(cookies, "res/cookies.json")
    await bootstrap_context.close()
    return cookies


async def create_worker(browser: Browser, stealth_js: str, cookies: list, worker_id: int) -> WorkerSession:
    context = await create_context(browser, stealth_js, cookies)
    page = await context.new_page()
    page.set_default_timeout(24 * 3600 * 1000)
    session = WorkerSession(worker_id=worker_id, context=context, page=page)

    verify_task = asyncio.create_task(wait_for_verify(page, config, session.verify_event, session.name))
    video_task = asyncio.create_task(video_optimize(page, config))
    answer_task = asyncio.create_task(skip_questions(page, session.answer_event))
    play_task = asyncio.create_task(play_video(page, session.name, session.restart_event))
    ocr_task = asyncio.create_task(status_ocr_stream(page, session.name, config.statusInterval, session.restart_event))
    session.tasks.extend([verify_task, video_task, answer_task, play_task, ocr_task])

    if config.enableHideWindow:
        try:
            window = await get_browser_window(page)
            session.tasks.append(asyncio.create_task(activate_window(window)))
            await hide_window(page)
        except Exception:
            pass

    return session


async def close_worker(session: WorkerSession) -> None:
    for task in session.tasks:
        task.cancel()
    if session.tasks:
        await asyncio.gather(*session.tasks, return_exceptions=True)
    try:
        await session.context.close()
    except Exception:
        pass


def reset_runtime_events(session: WorkerSession) -> None:
    session.verify_event.clear()
    session.answer_event.clear()
    session.restart_event.clear()


async def wait_runtime_block(session: WorkerSession) -> None:
    if await session.page.query_selector(".yidun_modal__title"):
        await session.verify_event.wait()
    elif await session.page.query_selector(".topic-title"):
        await session.answer_event.wait()


async def wait_lesson_completion(session: WorkerSession, title: str, start_time: float) -> str:
    seen_video = False
    while True:
        try:
            if session.restart_event.is_set():
                logger.warn(f"[{session.name}] 收到后台重建信号，立即重建窗口.", shift=True)
                return "restart"
            await wait_runtime_block(session)
            if 0 < config.limitMaxTime <= (time.time() - start_time) / 60:
                logger.info(f"[{session.name}] \"{title}\" 达到单次观看上限:{config.limitMaxTime}min", shift=True)
                return "time_limit"

            duration = await get_video_attr(session.page, "duration")
            current = await get_video_attr(session.page, "currentTime")
            paused = await get_video_attr(session.page, "paused")
            ended = await get_video_attr(session.page, "ended")
            if duration and current is not None:
                seen_video = True
                percent = 0 if duration <= 0 else int(min(100, max(0, current / duration * 100)))
                show_course_progress(f"[{session.name}] 当前节次进度:", f"{percent}%")
                if ended:
                    logger.info(f"[{session.name}] \"{title}\" 检测到 ended=True，准备重建窗口.", shift=True)
                    return "ended"
                if paused:
                    logger.info(f"[{session.name}] 监测到暂停态,等待自动续播.")
                if current >= max(duration - 2, duration * 0.98):
                    logger.info(f"[{session.name}] \"{title}\" 已进入完成终态，准备重建窗口.", shift=True)
                    return "restart"
            elif seen_video:
                logger.info(f"[{session.name}] \"{title}\" 视频元素消失，准备重建窗口.", shift=True)
                return "restart"
            await asyncio.sleep(2)
        except TimeoutError:
            await wait_runtime_block(session)


async def reload_course_page(session: WorkerSession, course_url: str, is_new_version=False, is_hike_class=False) -> None:
    await session.page.goto(course_url, wait_until="commit")
    await optimize_page(session.page, config, is_new_version, is_hike_class)


async def get_course_title(page: Page) -> str:
    selectors = (
        ".source-name",
        ".course-name",
        "h1",
        "h2",
        '[class*="title"]',
        '[class*="Title"]',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                text = await locator.text_content()
                if text and text.strip():
                    return text.strip()
        except Exception:
            continue
    try:
        title = await page.title()
        if title and title.strip():
            return title.strip()
    except Exception:
        pass
    return "未识别课程标题"


async def open_and_watch_lesson(
    session: WorkerSession,
    lesson: dict,
    claim_key: str,
    claim_manager: ClaimManager,
    course_url: str,
    is_new_version=False,
    is_hike_class=False,
) -> None:
    title = lesson["title"]
    result = "unknown"
    try:
        if not await click_card_by_id(
            session.page,
            lesson["card_id"],
            title,
            lesson.get("scope_id"),
            lesson.get("top"),
        ):
            logger.warn(f"[{session.name}] 未能定位卡片:{title}, 本轮跳过.", shift=True)
            return

        logger.info(
            f"[{session.name}] 锁定蓝条未满节次: {lesson.get('section', '')} / {title} ({lesson['progress']}%)"
        )
        await session.page.wait_for_timeout(1000)
        try:
            current_title = await get_lesson_name(session.page, is_hike_class) or title
        except Exception:
            current_title = title
        logger.info(f"[{session.name}] 开始观看:{current_title}")
        try:
            await session.page.wait_for_selector("video", state="attached", timeout=15000)
            await session.page.evaluate(config.remove_pause)
        except Exception:
            logger.warn(f"[{session.name}] 未及时检测到视频元素,进入宽松等待模式.", shift=True)
        start_time = time.time()
        result = await wait_lesson_completion(session, current_title, start_time)
    finally:
        await claim_manager.release(claim_key)
        if result in {"ended", "restart", "completed", "video_disappeared"}:
            raise WorkerRestart(f"{session.name} ended state restart")
        await reload_course_page(session, course_url, is_new_version, is_hike_class)


async def process_course(session: WorkerSession, course_url: str, claim_manager: ClaimManager) -> bool:
    is_new_version = "fusioncourseh5" in course_url
    is_hike_class = "hike.zhihuishu.com" in course_url
    logger.info(f"[{session.name}] 正在加载课程母页...")
    await session.page.goto(course_url, wait_until="commit")
    await optimize_page(session.page, config, is_new_version, is_hike_class)
    logger.info(f"[{session.name}] 当前页面: {session.page.url}")
    course_title = await get_course_title(session.page)
    logger.info(f"[{session.name}] 当前课程:<<{course_title}>>")

    logger.info(f"[{session.name}] 开始运行时滚动扫描...", shift=True)
    pending_lessons, summary = await scan_pending_lessons_deep(session.page)
    logger.info(
        f"[{session.name}] 整页统计: 总卡片 {summary['total']} | 未完成 {summary['pending']} | 已完成 {summary['done']}",
        shift=True,
    )
    if summary["sections"]:
        top_sections = []
        for section, stats in summary["sections"].items():
            top_sections.append(f"{section}:{stats['pending']}/{stats['total']}")
        logger.info(f"[{session.name}] 分组统计: {' | '.join(top_sections[:6])}")

    if not pending_lessons:
        return False

    for lesson in pending_lessons:
        claim_key = await claim_manager.claim(course_url, lesson)
        if not claim_key:
            continue
        reset_runtime_events(session)
        await open_and_watch_lesson(
            session,
            lesson,
            claim_key,
            claim_manager,
            course_url,
            is_new_version,
            is_hike_class,
        )
        return True
    return False


async def worker_loop(
    session: WorkerSession,
    browser: Browser,
    stealth_js: str,
    cookies: list,
    claim_manager: ClaimManager,
    start_offset: int,
    all_tasks: list[asyncio.Task],
) -> None:
    course_total = len(config.course_urls)
    while True:
        if session.restart_event.is_set():
            raise WorkerRestart(f"{session.name} restart requested")
        did_work = False
        for offset in range(course_total):
            if session.restart_event.is_set():
                raise WorkerRestart(f"{session.name} restart requested")
            course_url = config.course_urls[(start_offset + offset) % course_total]
            try:
                if await process_course(session, course_url, claim_manager):
                    did_work = True
            except WorkerRestart:
                logger.warn(f"[{session.name}] 检测到播放结束态，销毁并重建 worker.", shift=True)
                try:
                    await session.page.close()
                except Exception:
                    pass
                await close_worker(session)
                await asyncio.sleep(1)
                new_session = await create_worker(browser, stealth_js, cookies, session.worker_id)
                session.context = new_session.context
                session.page = new_session.page
                session.verify_event = new_session.verify_event
                session.answer_event = new_session.answer_event
                session.restart_event = new_session.restart_event
                session.tasks = new_session.tasks
                all_tasks.extend(new_session.tasks)
                did_work = True
                break
            except TargetClosedError:
                logger.warn(f"[{session.name}] 页面/上下文已关闭，立即重建 worker.", shift=True)
                await close_worker(session)
                await asyncio.sleep(1)
                new_session = await create_worker(browser, stealth_js, cookies, session.worker_id)
                session.context = new_session.context
                session.page = new_session.page
                session.verify_event = new_session.verify_event
                session.answer_event = new_session.answer_event
                session.restart_event = new_session.restart_event
                session.tasks = new_session.tasks
                all_tasks.extend(new_session.tasks)
                did_work = True
                break
            except Exception as e:
                logger.error(f"[{session.name}] {repr(e)}", shift=True)
                logger.write_log(traceback.format_exc())
        if not did_work:
            logger.info(f"[{session.name}] 本轮未领取到新任务, {config.scanInterval}s 后继续轮询.", shift=True)
            await asyncio.sleep(config.scanInterval)


async def main() -> None:
    async with async_playwright() as p:
        browser, stealth_js = await launch_browser(p)
        cookies = await bootstrap_login(browser, stealth_js)

        worker_total = max(1, config.parallelTasks)
        logger.info(f"并行窗口上限: {worker_total}")

        sessions = []
        all_tasks: list[asyncio.Task] = []
        claim_manager = ClaimManager()

        for worker_id in range(1, worker_total + 1):
            session = await create_worker(browser, stealth_js, cookies, worker_id)
            sessions.append(session)
            all_tasks.extend(session.tasks)

        worker_tasks = [
            asyncio.create_task(
                worker_loop(session, browser, stealth_js, cookies, claim_manager, session.worker_id - 1, all_tasks)
            )
            for session in sessions
        ]
        all_tasks.extend(worker_tasks)

        monitor_task = asyncio.create_task(task_monitor(all_tasks))
        await asyncio.gather(*worker_tasks)
        await monitor_task


if __name__ == "__main__":
    print("Github:CXRunfree All Rights Reserved.")
    try:
        logger.info("程序启动中...")
        config = Config("configs.ini")
        if not config.course_urls:
            logger.info("未检测到有效网址或不支持此类网页,请检查配置文件!")
            time.sleep(2)
            sys.exit(-1)
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warn("收到手动中断,程序退出.", shift=True)
    except TargetClosedError as e:
        logger.write_log(traceback.format_exc())
        if "BrowserType.launch" in repr(e):
            logger.error("浏览器启动失败,请尝试重新启动!")
            logger.info("如果仍然无法启动,请修改配置文件并使用Chrome浏览器")
        else:
            logger.error("浏览器被关闭,程序退出.")
    except Exception as e:
        logger.error(repr(e), shift=True)
        logger.write_log(traceback.format_exc())
        if isinstance(e, KeyError):
            logger.error("配置文件错误!")
        elif isinstance(e, FileNotFoundError):
            logger.error(f"依赖文件缺失: {e.filename},请重新安装程序!")
        elif isinstance(e, UnicodeDecodeError):
            logger.error("配置文件编码错误,保存时请选择UTF-8或GBK编码!")
        else:
            logger.error("系统出错,请检查后重新启动!")
    finally:
        logger.save()
        show_donate("res/QRcode.jpg")
        input("程序已结束,按Enter退出...")
