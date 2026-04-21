import os
import shutil

name = "Autovisor"

cmd = (
    "pyinstaller "
    "--log-level=INFO "
    "--noconfirm "
    "-c "
    "-i ./res/zhs.ico "
    "--onedir "
    "--contents-directory=internal "
    f"--name={name} "
    "./Autovisor.py"
)
os.system(cmd)

os.makedirs(f"./dist/{name}/res", exist_ok=True)
open(f"./dist/{name}/建议使用Chrome浏览器启动", "w", encoding="utf-8").close()
shutil.copyfile("./res/QRcode.jpg", f"./dist/{name}/res/QRcode.jpg")
shutil.copyfile("./configs.ini", f"./dist/{name}/configs.ini")
shutil.copyfile("./res/stealth.min.js", f"./dist/{name}/res/stealth.min.js")
shutil.rmtree("./build", ignore_errors=True)
if os.path.exists("./Autovisor.spec"):
    os.remove("./Autovisor.spec")
