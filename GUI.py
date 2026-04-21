import configparser
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import sv_ttk

config = configparser.ConfigParser()
config_file = "configs.ini"
config.read(config_file, encoding="utf-8")

default_driver = "Chrome"
default_exepath = ""


def show_help():
    help_text = (
        "【必要配置说明】\n"
        "账号密码：可选，留空则手动登录。\n"
        "课程链接：填写课程播放页链接。\n"
        "时长限制：单次锁定某一节的最大观看时长，0 表示不限。\n"
        "倍速播放：最大 1.8。\n"
        "重扫间隔：母页无新任务时多久后重扫。\n"
        "并行任务数：多窗口并行上限。\n"
        "OCR间隔：控制台实时状态输出周期。\n\n"
        "【运行逻辑】\n"
        "程序会启动多个窗口协同扫描课程母页，抢占未完成节次并自动播放。\n"
        "每个窗口每隔数秒输出一次实时状态，包含暂停/完成检测。"
    )
    messagebox.showinfo("使用说明", help_text)


def launch_script():
    messagebox.showinfo("启动中", "准备开始多窗口任务流")
    os.system(f'"{sys.executable}" Autovisor.py')


def launch_script_in_thread():
    threading.Thread(target=launch_script, daemon=True).start()


def launch_direct():
    def run():
        messagebox.showinfo("提示", "已记录配置，开始运行")
        os.system(f'"{sys.executable}" Autovisor.py')

    threading.Thread(target=run, daemon=True).start()


def read_inputs():
    return {
        "username": username_entry.get(),
        "password": password_entry.get(),
        "course_url": course_entry.get(),
        "limit_time": time_limit_entry.get(),
        "speed": speed_entry.get(),
        "scan_interval": scan_interval_entry.get(),
        "parallel_tasks": parallel_tasks_entry.get(),
        "status_interval": status_interval_entry.get(),
        "hide_window": hide_var.get(),
        "mute": mute_var.get(),
    }


def save_and_run():
    inputs = read_inputs()
    config.set("course-url", "URL1", inputs["course_url"])
    config.set("user-account", "username", inputs["username"])
    config.set("user-account", "password", inputs["password"])
    config.set("browser-option", "driver", default_driver)
    config.set("browser-option", "exe_path", default_exepath)
    config.set("script-option", "enablehidewindow", inputs["hide_window"])
    config.set("script-option", "scaninterval", inputs["scan_interval"])
    config.set("script-option", "paralleltasks", inputs["parallel_tasks"])
    config.set("script-option", "statusinterval", inputs["status_interval"])
    config.set("course-option", "limitmaxtime", inputs["limit_time"])
    config.set("course-option", "limitspeed", inputs["speed"])
    config.set("course-option", "soundoff", inputs["mute"])

    with open(config_file, "w", encoding="utf-8") as f:
        config.write(f)

    launch_script_in_thread()


root = tk.Tk()
root.title("智慧树多窗口任务流助手")
root.geometry("720x580+80+60")
root.resizable(False, False)
sv_ttk.set_theme("light")
ttk.Label(root, text="智慧树多窗口任务流助手", font=("Microsoft YaHei", 20)).pack(pady=25)

frame = ttk.Frame(root)
frame.pack()

ttk.Label(frame, text="必要配置:", font=("Microsoft YaHei", 15)).grid(row=0, column=1)

ttk.Label(frame, text="手机号：", font=("Microsoft YaHei", 10)).grid(row=1, column=1)
username_entry = ttk.Entry(frame)
username_entry.grid(row=1, column=3)

ttk.Label(frame, text="密码：", font=("Microsoft YaHei", 10)).grid(row=2, column=1)
password_entry = ttk.Entry(frame, show="*")
password_entry.grid(row=2, column=3)

ttk.Label(frame, text="课程链接：", font=("Microsoft YaHei", 10)).grid(row=3, column=1)
course_entry = ttk.Entry(frame, width=42)
course_entry.grid(row=3, column=3)

ttk.Label(frame, text="时长限制(min)：", font=("Microsoft YaHei", 10)).grid(row=4, column=1)
time_limit_entry = ttk.Entry(frame)
time_limit_entry.grid(row=4, column=3)

ttk.Label(frame, text="倍速：", font=("Microsoft YaHei", 10)).grid(row=5, column=1)
speed_entry = ttk.Entry(frame)
speed_entry.grid(row=5, column=3)

ttk.Label(frame, text="重扫间隔(s)：", font=("Microsoft YaHei", 10)).grid(row=6, column=1)
scan_interval_entry = ttk.Entry(frame)
scan_interval_entry.grid(row=6, column=3)

ttk.Label(frame, text="并行任务数：", font=("Microsoft YaHei", 10)).grid(row=7, column=1)
parallel_tasks_entry = ttk.Entry(frame)
parallel_tasks_entry.grid(row=7, column=3)

ttk.Label(frame, text="OCR间隔(s)：", font=("Microsoft YaHei", 10)).grid(row=8, column=1)
status_interval_entry = ttk.Entry(frame)
status_interval_entry.grid(row=8, column=3)

ttk.Label(frame, text="", font=("Microsoft YaHei", 10)).grid(row=9, column=1, pady=15)

hide_var = tk.StringVar(value="False")
mute_var = tk.StringVar(value="False")

ttk.Label(frame, text="可选配置:", font=("Microsoft YaHei", 15)).grid(row=9, column=1)

ttk.Label(frame, text="隐藏浏览器窗口：", font=("Microsoft YaHei", 10)).grid(row=10, column=1)
ttk.Radiobutton(frame, text="True", variable=hide_var, value="True").grid(row=10, column=3, sticky="w")
ttk.Radiobutton(frame, text="False", variable=hide_var, value="False").grid(row=10, column=3, sticky="e")

ttk.Label(frame, text="静音播放：", font=("Microsoft YaHei", 10)).grid(row=11, column=1)
ttk.Radiobutton(frame, text="True", variable=mute_var, value="True").grid(row=11, column=3, sticky="w")
ttk.Radiobutton(frame, text="False", variable=mute_var, value="False").grid(row=11, column=3, sticky="e")

button_frame = ttk.Frame(root)
button_frame.pack(pady=20)

save_button = ttk.Button(button_frame, text="保存配置并启动", command=save_and_run)
save_button.pack(side=tk.LEFT, padx=10)

help_button = ttk.Button(button_frame, text="查看帮助", command=show_help)
help_button.pack(side=tk.LEFT, padx=10, expand=True)

direct_button = ttk.Button(button_frame, text="直接启动", command=launch_direct)
direct_button.pack(side=tk.RIGHT, padx=10)

root.bind("<Return>", lambda event: save_and_run())

root.mainloop()
