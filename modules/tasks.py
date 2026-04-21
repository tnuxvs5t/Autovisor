import asyncio

from playwright._impl._errors import TargetClosedError
from playwright.async_api import Page
from pygetwindow import Win32Window

from modules.configs import Config
from modules.logger import Logger
from modules.utils import display_window, get_video_attr, hide_window

logger = Logger()

STATUS_PROBE_JS = r"""
() => {
    const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
    const visibleText = [];
    for (const el of Array.from(document.querySelectorAll('body *'))) {
        if (visibleText.length >= 5) break;
        const text = normalize(el.textContent || '');
        if (!text) continue;
        if (!(text.includes('学习进度') || text.includes('掌握度') || text.includes('%'))) continue;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (rect.width < 40 || rect.height < 16) continue;
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        if (!visibleText.includes(text)) {
            visibleText.push(text.slice(0, 80));
        }
    }

    const titleSelectors = ['#lessonOrder', '.current_play [title]', '.current_play', 'h1', 'h2', '[title]'];
    let lessonTitle = '';
    for (const selector of titleSelectors) {
        const el = document.querySelector(selector);
        if (!el) continue;
        lessonTitle = normalize(el.getAttribute && el.getAttribute('title') ? el.getAttribute('title') : el.textContent);
        if (lessonTitle) break;
    }

    const video = document.querySelector('video');
    const currentTime = video ? Number(video.currentTime || 0) : null;
    const duration = video ? Number(video.duration || 0) : null;
    const percent = video && duration > 0 ? Math.floor((currentTime / duration) * 100) : null;
    return {
        pageTitle: normalize(document.title || ''),
        lessonTitle,
        hasVideo: !!video,
        paused: video ? !!video.paused : null,
        ended: video ? !!video.ended : null,
        currentTime,
        duration,
        percent,
        text: visibleText,
        url: location.href,
    };
}
"""


async def trigger_restart(page: Page, worker_name: str, restart_event: asyncio.Event | None = None) -> None:
    if restart_event is not None and restart_event.is_set():
        return
    if restart_event is not None:
        restart_event.set()
    logger.warn(f"[{worker_name}] 触发强制重建，立即关闭当前上下文.", shift=True)
    try:
        await page.context.close()
    except Exception:
        pass


async def task_monitor(tasks: list[asyncio.Task]) -> None:
    checked_tasks = set()
    logger.info("任务监控已启动.")
    while any(not task.done() for task in tasks):
        for task in tasks:
            if task.done() and task not in checked_tasks:
                checked_tasks.add(task)
                exc = task.exception()
                if exc is None:
                    continue
                func_name = task.get_coro().__name__
                logger.error(f"任务函数{func_name} 出现异常.", shift=True)
                logger.write_log(f"{repr(exc)}\n")
        await asyncio.sleep(1)
    logger.info("任务监控已退出.", shift=True)


async def activate_window(window: Win32Window) -> None:
    while True:
        try:
            await asyncio.sleep(2)
            if window and window.isMinimized:
                window.moveTo(-3200, -3200)
                await asyncio.sleep(0.3)
                window.restore()
                logger.info("检测到播放窗口最小化,已自动恢复.")
        except TargetClosedError:
            logger.write_log("浏览器已关闭,窗口激活模块已下线.\n")
            return
        except Exception:
            continue


async def video_optimize(page: Page, config: Config) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(2)
            await page.wait_for_selector("video", state="attached", timeout=3000)
            volume = await get_video_attr(page, "volume")
            rate = await get_video_attr(page, "playbackRate")
            if config.soundOff and volume != 0:
                await page.evaluate(config.volume_none)
                await page.evaluate(config.set_none_icon)
            if rate != config.limitSpeed:
                await page.evaluate(config.revise_speed)
                await page.evaluate(config.revise_speed_name)
        except TargetClosedError:
            logger.write_log("浏览器已关闭,视频调节模块已下线.\n")
            return
        except Exception:
            continue


async def play_video(page: Page, worker_name: str = "W?", restart_event: asyncio.Event | None = None) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(2)
            await page.wait_for_selector("video", state="attached", timeout=1000)
            ended = await page.evaluate("document.querySelector('video').ended")
            paused = await page.evaluate("document.querySelector('video').paused")
            if ended:
                await trigger_restart(page, worker_name, restart_event)
                continue
            if paused:
                logger.info(f"[{worker_name}] 检测到视频暂停,正在尝试播放.")
                await page.evaluate(
                    """() => {
                        const video = document.querySelector('video');
                        if (video && !video.ended) {
                            video.play().catch(() => {});
                        }
                    }"""
                )
                logger.write_log(f"{worker_name} 视频已恢复播放.\n")
        except TargetClosedError:
            logger.write_log(f"{worker_name} 浏览器已关闭,视频播放模块已下线.\n")
            return
        except Exception:
            continue


async def skip_questions(page: Page, event_loop) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            if "hike.zhihuishu.com" in page.url:
                logger.warn("当前课程为新版本,不支持自动答题.", shift=True)
                return
            await asyncio.sleep(2)
            ques_element = await page.wait_for_selector(".el-scrollbar__view", state="attached", timeout=1000)
            total_ques = await ques_element.query_selector_all(".number")
            if total_ques:
                logger.write_log(f"检测到{len(total_ques)}道题目.\n")
            for ques in total_ques:
                await ques.click(timeout=500)
                if not await page.query_selector(".answer"):
                    choices = await page.query_selector_all(".topic-item")
                    for each in choices[:2]:
                        await each.click(timeout=500)
                        await page.wait_for_timeout(100)
            await page.press(".el-dialog", "Escape", timeout=1000)
            event_loop.set()
        except TargetClosedError:
            logger.write_log("浏览器已关闭,答题模块已下线.\n")
            return
        except Exception:
            if "fusioncourseh5" in page.url:
                not_finish_close = await page.query_selector(".el-dialog")
                if not_finish_close:
                    await page.press(".el-dialog", "Escape", timeout=1000)
            elif "hike.zhihuishu.com" in page.url:
                logger.warn("当前课程为新版本,不支持自动答题.", shift=True)
                return
            else:
                not_finish_close = await page.query_selector(".el-message-box__headerbtn")
                if not_finish_close:
                    await not_finish_close.click()
            continue


async def wait_for_verify(page: Page, config, event_loop, worker_name: str = "W?") -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(3)
            await page.wait_for_selector(".yidun_modal__title", state="attached", timeout=1000)
            logger.warn(f"[{worker_name}] 检测到安全验证,请手动完成验证...", shift=True)
            if config.enableHideWindow:
                await display_window(page)
            await page.wait_for_selector(".yidun_modal__title", state="hidden", timeout=24 * 3600 * 1000)
            event_loop.set()
            if config.enableHideWindow:
                await hide_window(page)
            logger.info(f"[{worker_name}] 安全验证已完成.", shift=True)
            await asyncio.sleep(30)
        except TargetClosedError:
            logger.write_log(f"{worker_name} 浏览器已关闭,安全验证模块已下线.\n")
            return
        except Exception:
            continue


async def status_ocr_stream(
    page: Page,
    worker_name: str,
    interval_sec: int = 5,
    restart_event: asyncio.Event | None = None,
) -> None:
    await page.wait_for_load_state("domcontentloaded")
    while True:
        try:
            await asyncio.sleep(interval_sec)
            data = await page.evaluate(STATUS_PROBE_JS)
            if data["hasVideo"]:
                cur = 0 if data["currentTime"] is None else data["currentTime"]
                dur = 0 if data["duration"] is None else data["duration"]
                logger.info(
                    f"[{worker_name}/OCR] {data['lessonTitle'] or data['pageTitle']} | "
                    f"{data['percent'] if data['percent'] is not None else 0}% | "
                    f"paused={data['paused']} | ended={data['ended']} | {cur:.0f}/{dur:.0f}s"
                )
                if data["ended"]:
                    logger.warn(f"[{worker_name}/OCR] 检测到 ended=True，等待 worker 重建.", shift=True)
                    await trigger_restart(page, worker_name, restart_event)
            elif data["text"]:
                logger.info(f"[{worker_name}/OCR] {' | '.join(data['text'][:3])}")
        except TargetClosedError:
            logger.write_log(f"{worker_name} status stream offline.\n")
            return
        except Exception:
            continue
