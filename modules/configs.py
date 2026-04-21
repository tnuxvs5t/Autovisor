# encoding=utf-8
import configparser
import re


class Config:
    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        self._config = configparser.ConfigParser()
        if config_path:
            self._read_config()
            self.driver = self.get_driver()
            self.username = self._config.get("user-account", "username", raw=True, fallback="").strip()
            self.password = self._config.get("user-account", "password", raw=True, fallback="").strip()
            self.exe_path = self._config.get("browser-option", "EXE_PATH", raw=True, fallback="").strip()
            self.enableHideWindow = self.get_bool_field("script-option", "enableHideWindow", False)
            self.scanInterval = self.get_int_field("script-option", "scanInterval", 15)
            self.parallelTasks = self.get_int_field("script-option", "parallelTasks", 2)
            self.statusInterval = self.get_int_field("script-option", "statusInterval", 5)
            self.soundOff = self.get_bool_field("course-option", "soundOff", True)
            self.course_match_rule = re.compile(r"https://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]")
            self.course_urls = self.get_course_urls()
        else:
            self.driver = "chrome"
            self.username = ""
            self.password = ""
            self.exe_path = ""
            self.enableHideWindow = False
            self.scanInterval = 15
            self.parallelTasks = 2
            self.statusInterval = 5
            self.soundOff = True
            self.course_match_rule = re.compile(r"https://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]")
            self.course_urls = []

        self.login_url = "https://passport.zhihuishu.com/login"
        self.pop_js = """document.getElementsByClassName("iconfont iconguanbi")[0].click();"""
        self.close_ques = """document.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, keyCode: 27 }));"""
        self.remove_pause = "document.querySelector('video').pause = ()=>{}"
        self.play_video = """const video = document.querySelector('video');video.play();"""
        self.volume_none = "document.querySelector('video').volume=0;"
        self.set_none_icon = """document.querySelector(".volumeBox").classList.add("volumeNone")"""
        self.reset_curtime = """document.querySelector('video').currentTime=0;"""

    def _read_config(self) -> None:
        try:
            self._config.read(self.config_path, encoding="utf-8")
        except UnicodeDecodeError:
            self._config.read(self.config_path, encoding="gbk")

    def get_driver(self) -> str:
        driver = self._config.get("browser-option", "driver", raw=True, fallback="edge").strip()
        return driver.lower() if driver else "edge"

    def get_bool_field(self, section: str, option: str, default: bool = False) -> bool:
        field = self._config.get(section, option, raw=True, fallback="").strip().lower()
        if not field:
            return default
        return field == "true"

    def get_int_field(self, section: str, option: str, default: int = 0) -> int:
        value = self._config.get(section, option, raw=True, fallback="").strip()
        if not value:
            return default
        try:
            return max(1, int(value))
        except ValueError:
            return default

    def get_course_urls(self) -> list[str]:
        course_urls: list[str] = []
        if not self._config.has_section("course-url"):
            return course_urls
        for option in self._config.options("course-url"):
            course_url = self._config.get("course-url", option, raw=True, fallback="").strip()
            if not course_url:
                continue
            matched = re.findall(self.course_match_rule, course_url)
            if not matched:
                print(f"\"{course_url}\"\n不是一个有效网址,将忽略该网址.")
                continue
            course_urls.append(course_url)
        return course_urls

    def _safe_get_float(self, section: str, option: str, default: float = 0.0) -> float:
        try:
            value = self._config.get(section, option, raw=True, fallback="").strip()
            if not value:
                return default
            return float(value)
        except (ValueError, configparser.Error):
            return default

    @property
    def limitMaxTime(self) -> float:
        if self.config_path:
            self._read_config()
        return self._safe_get_float("course-option", "limitMaxTime", 0.0)

    @property
    def limitSpeed(self) -> float:
        if self.config_path:
            self._read_config()
        speed = self._safe_get_float("course-option", "limitSpeed", 1.0)
        return min(max(speed, 0.5), 1.8)

    @property
    def revise_speed(self) -> str:
        return f"document.querySelector('video').playbackRate={self.limitSpeed};"

    @property
    def revise_speed_name(self) -> str:
        return f"""document.querySelector(".speedBox span").innerText = "X {self.limitSpeed}";"""
