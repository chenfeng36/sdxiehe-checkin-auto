# -*- coding: utf-8 -*-
# =====================================================================================
# 多账号签到调度器（配合各账号文件夹里的“自动签到.exe”使用）
#
# 功能：读取同目录 accounts.txt 里的账号列表，按顺序依次运行每个账号的签到程序；
#       前一个程序一退出就立即运行下一个（无缝衔接），全部结束后自动退出。
#
# 使用：
#   1. 编辑 accounts.txt，一行一个签到程序的完整路径（# 开头为注释）；
#   2. 双击“批量签到.exe”运行（或 python multi_checkin.py）；
#      任务计划里也只需要挂这一个程序。
#
# 生成/使用的文件（都在 exe 或脚本所在目录）：
#   accounts.txt    账号列表；首次运行自动生成模板
#   调度日志.log     调度记录（每个账号的耗时、结果）
#   （各账号自己的签到结果仍看它们各自文件夹里的 log.log 和截图）
# =====================================================================================
import argparse
import os
import subprocess
import sys
import time
import traceback

import msvcrt

#打包后：accounts.txt / 日志 都放在 exe 所在目录
if getattr(sys, "frozen", False):
    base_dir = os.path.dirname(os.path.abspath(sys.executable))
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

accounts_file_name = os.path.join(base_dir, "accounts.txt")     #账号列表
log_file_name = os.path.join(base_dir, "调度日志.log")           #调度日志
lock_file_name = os.path.join(base_dir, "调度器运行中.lock")      #防重复运行的锁文件

gap_seconds = 3             #两个账号之间的间隔（秒）：留点时间让浏览器完全退出、cookie 写盘
per_account_timeout = 5*60  #单个账号最长运行时间（秒），超时会强制结束并继续下一个

#签到程序退出码的含义（对应新版“自动签到”；旧版程序只会返回 0）
exit_code_text = {0:"成功/已签到", 1:"失败/异常", 2:"配置缺失", 3:"未到签到时间"}

lock_file = None    #调度器自身的运行锁（模块级持有，进程退出自动释放）

def write_log(text):
    """把一行记录写入 调度日志.log（行首加时间），失败只提示不中断"""
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file_name, "a", encoding="utf-8") as file:
            file.write(stamp + " | " + text + "\n")
    except Exception:
        print("调度日志写入失败")

def acquire_lock():
    """防止重复运行：用文件锁实现（进程退出自动释放，残留的锁文件无影响）"""
    global lock_file
    try:
        lock_file = open(lock_file_name, "w")
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except Exception:
        return False

def load_accounts():
    """读取 accounts.txt，返回路径列表；文件不存在返回 None（首次运行会生成模板）

    规则：一行一个路径；# 开头为注释；空行忽略；自动去掉首尾空白和用户习惯加的引号。
    """
    if not os.path.exists(accounts_file_name):
        return None
    accounts = []
    with open(accounts_file_name, "r", encoding="utf-8") as file:
        for line in file:
            item = line.strip()
            if not item or item.startswith("#"):
                continue
            if len(item) >= 2 and item[0] == '"' and item[-1] == '"':
                item = item[1:-1].strip()
            accounts.append(item)
    return accounts

def write_template():
    """首次运行：生成 accounts.txt 模板"""
    with open(accounts_file_name, "w", encoding="utf-8") as file:
        file.write(
            "# ============================================\n"
            "# 多账号签到 · 账号列表\n"
            "# 一行写一个“自动签到.exe”的完整路径；\n"
            "# 以 # 开头的行是注释，空行会被忽略；\n"
            "# 顺序就是签到顺序，改好保存后双击“批量签到.exe”即可。\n"
            "# ============================================\n"
            "\n"
            "# 示例（把下面的 # 去掉，改成你自己的路径）：\n"
            "# D:\\签到\\账号A\\自动签到.exe\n"
            "# D:\\签到\\账号B\\自动签到.exe\n"
            "# D:\\签到\\账号C\\自动签到.exe\n"
        )

def read_last_result(exe_path):
    """读取该账号自己目录里 log.log 的最后一行（作为结果摘要），读不到返回空"""
    try:
        log_path = os.path.join(os.path.dirname(exe_path), "log.log")
        with open(log_path, "r", encoding="utf-8", errors="replace") as file:
            lines = [line.strip() for line in file if line.strip()]
        return lines[-1] if lines else ""
    except Exception:
        return ""

def run_account(index, total, exe_path, extra_args):
    """运行一个账号的签到程序并等它自己退出

    Returns:
        (结果文字, 耗时秒, 是否成功)
    """
    print("=" * 64)
    print("[%d/%d] 开始：%s" % (index, total, exe_path))
    write_log("[%d/%d] 开始：%s" % (index, total, exe_path))

    if not os.path.exists(exe_path):
        print("[%d/%d] 找不到这个文件，跳过" % (index, total))
        write_log("[%d/%d] 文件不存在，跳过：%s" % (index, total, exe_path))
        return "文件不存在", 0.0, False

    start = time.time()
    try:
        #独立控制台：签到程序最小化自己的窗口时，不会影响调度器窗口
        proc = subprocess.Popen([exe_path] + extra_args,
                                cwd=os.path.dirname(exe_path),
                                creationflags=subprocess.CREATE_NEW_CONSOLE)
    except Exception as e:
        print("[%d/%d] 启动失败：%s" % (index, total, e))
        write_log("[%d/%d] 启动失败：%s（%s）" % (index, total, exe_path, e))
        return "启动失败", 0.0, False

    timed_out = False
    try:
        code = proc.wait(timeout=per_account_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        print("[%d/%d] 运行超过 %d 秒仍未结束，强制结束它，继续下一个" % (index, total, per_account_timeout))
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        try:
            code = proc.wait(timeout=30)
        except Exception:
            code = -1    #taskkill 后仍拿不到退出码时用 -1 兜底（超时场景的结果文案固定，不受影响）

    elapsed = time.time() - start
    if timed_out:
        result = "超时（已强制结束）"
    else:
        result = exit_code_text.get(code, "退出码 %s" % code)

    last_line = read_last_result(exe_path)
    if last_line:
        result += "；该账号日志：%s" % last_line

    print("[%d/%d] 结束：耗时 %.0f 秒，%s" % (index, total, elapsed, result))
    write_log("[%d/%d] 结束：耗时 %.0f 秒，%s（%s）" % (index, total, elapsed, result, exe_path))
    return result, elapsed, (code == 0 and not timed_out)

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="多账号签到调度器")
    parser.add_argument("--dry-run", action="store_true",
                        help="给每个签到程序加 --dry-run（只检测不点击；需要各账号使用新版程序）")
    return parser.parse_args()

def main():
    args = parse_args()
    if not acquire_lock():
        print("检测到已有调度在运行，本次启动直接退出（避免两边同时签到）")
        return 1

    accounts = load_accounts()
    if accounts is None:
        write_template()
        print("首次运行：已生成 accounts.txt")
        print("请打开它，把路径改成各账号签到程序的位置（一行一个），保存后重新运行")
        if sys.stdin.isatty():
            input("按回车键退出...")
        return 2
    if not accounts:
        print("accounts.txt 里还没有填有效路径，请填好后重新运行")
        print("（一行一个“自动签到.exe”的完整路径，# 开头的行是注释）")
        if sys.stdin.isatty():
            input("按回车键退出...")
        return 2

    extra_args = ["--dry-run"] if args.dry_run else []
    print("多账号签到调度器：共 %d 个账号" % len(accounts))
    write_log("开始批量签到：共 %d 个账号%s" % (len(accounts), "（试运行模式）" if args.dry_run else ""))

    start = time.time()
    failed = []
    for index, exe_path in enumerate(accounts, 1):
        _, _, ok = run_account(index, len(accounts), exe_path, extra_args)
        if not ok:
            failed.append(exe_path)
        if index < len(accounts):
            time.sleep(gap_seconds)     #留一点间隔，让浏览器完全退出

    total_time = time.time() - start
    print("=" * 64)
    summary = "全部完成：成功 %d / 共 %d，总耗时 %.0f 秒（%.1f 分钟）" % (
        len(accounts) - len(failed), len(accounts), total_time, total_time / 60.0)
    print(summary)
    write_log(summary)
    if failed:
        print("以下账号没有成功，请到它们各自的文件夹里看 log.log 和截图：")
        for exe_path in failed:
            print("  - " + exe_path)
            write_log("未成功：" + exe_path)
    return 0 if not failed else 1

if __name__ == "__main__":
    #只有直接运行本文件才会执行（被 import 时不执行）
    exit_code = 1
    try:
        exit_code = main()
    except Exception:
        traceback.print_exc()
        exit_code = 1
        if getattr(sys, "frozen", False) and sys.stdin.isatty():
            input("程序出现异常，按回车键退出...")#双击 exe 运行时防止窗口一闪而过
    sys.exit(exit_code)
