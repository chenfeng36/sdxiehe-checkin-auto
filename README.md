# 山东协和学院晚查寝自动签到程序

自动打开浏览器完成「智能查寝」签到：自动登录、注入签到位置、识别并点击签到按钮、
记录签到结果。滑块人机验证由**本地图像算法**完成——不联网、不调用任何第三方 API。

## 功能

- **自动登录**：账号密码保存在本地 `config.json`（支持明文或 base64），登录状态缓存到 `login.json`（约一周有效，免重复登录）；
  登录等待带超时（默认 5 分钟）与密码错误检测，不会无限挂起
- **定位注入**：通过浏览器 geolocation 注入签到位置，经纬度可在 `config.json` 修改
- **签到按钮识别**：页面同时存在「签到」「晚归签到」等多个按钮时也能正确选择并点击；按钮文案带后缀/空格也能匹配；
  点击后会二次确认按钮状态，无法确认时日志会注明「已点击未确认」
- **滑块验证自动通过**：读取验证码画布像素 → 用零均值归一化相关（NCC）定位拼图缺口（精确到 1 像素）→ 拟人轨迹一次拖到位；失败自动刷新重试
- **结果留痕**：`log.log` 记录「时间 | 账号 | 结果 | 用时」；每次运行保存 `日期_结果.png` 截图（如 `2026-09-22_签到成功.png`），
  运行中途出错也会留日志和截图
- **防重复运行**：同时启动两个实例时，后启动的会自动退出，避免两个浏览器互相干扰
- **可打包 exe**：PyInstaller 单文件打包，双击即用，无需装 Python

## 运行环境

- Windows 10 / 11，且装有 Microsoft Edge（脚本用 `channel="msedge"` 调用系统 Edge，不额外下载浏览器）
- 源码运行需要 Python 3.9+

## 安装依赖

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

> `requirements.txt` 里的 `setuptools<81` 是必需的：`win10toast` 依赖 `pkg_resources`，而 setuptools 81+ 已移除它。

## 使用

1. 复制 `config.example.json` 为 `config.json`，填入自己的账号密码：

   ```json
   {
       "username": "你的学号",
       "password": "你的密码",
       "location": { "longitude": "117.261944", "latitude": "36.739722" },
       "monkey_verify": true
   }
   ```

   其他可选字段（不写就用默认值，程序运行后会自动补全）：

   | 字段 | 默认值 | 说明 |
   | --- | --- | --- |
   | `monkey_verify` | `false` | `true` 自动过滑块（推荐）；`false` 弹通知由人工滑动 |
   | `checkin_url` | 学校签到页 | 学校换域名/换页面时可修改 |
   | `max_retry_times` | `40` | 「不在签到范围内」时最多重试次数（每 10 秒一次）；负数=一直等到能签到 |
   | `login_wait_timeout` | `300` | 等待登录完成的最长秒数，超时视为登录失败 |

   - 不填账号密码（保持 `unknow`）也能用：运行后按提示在弹出的浏览器里手动登录一次
   - 首次运行若没有 `config.json`，程序会生成模板并等待你填好后再重新运行

2. 运行：

   ```powershell
   .venv\Scripts\python autoCheckIn.py
   ```

   可选参数：

   ```powershell
   .venv\Scripts\python autoCheckIn.py --dry-run    # 试运行：只检测按钮不点击
   .venv\Scripts\python autoCheckIn.py --headless   # 无头模式（不显示浏览器窗口）
   ```

   或者在 [Releases](https://github.com/chenfeng36/sdxiehe-checkin-auto/releases) 里下载打包好的
   `autoCheckIn.exe`（说明文档为 `instructions.txt`）双击运行；下载后可自行重命名为「自动签到.exe」。

   > GitHub 不支持中文附件名（会被自动替换成 `default`），因此 Release 附件使用 ASCII 文件名。

3. 查看结果：
   - `log.log` 每行格式：`2026-09-22 21:30:12 | 账号[学号] | 签到成功 | 用时 23.4 秒`
   - 截图文件：`日期_结果.png`（如 `2026-09-22_签到成功.png`）
   - 进程退出码：`0`=成功/已签到；`1`=失败/异常；`2`=配置缺失（已生成模板）；`3`=未到签到时间

**安全试运行**（只检测页面状态，不会点击签到）：

```powershell
$env:CHECKIN_DRY_RUN=1; python autoCheckIn.py
# 或
python autoCheckIn.py --dry-run
```

> 注意：试运行仍会正常登录（会提交账号密码、自动过滑块），只是不会点击签到按钮。

## 打包 exe

```powershell
.venv\Scripts\pyinstaller.exe --noconfirm --onefile --console --name "自动签到" `
    --collect-all playwright --collect-all win10toast `
    --hidden-import win32console --hidden-import win32timezone --hidden-import pkg_resources `
    --hidden-import win32api --hidden-import win32event `
    autoCheckIn.py
```

## 调试脚本

| 脚本 | 用途 |
| --- | --- |
| `captcha_debug.py` | 抓取滑块验证码的原图、DOM 结构与几何信息（用于开发识别算法） |
| `captcha_solve_test.py` | 真实登录页上实测滑块识别成功率 |
| `captcha_match_test4.py` | 缺口定位算法（NCC）离线版本，可在已抓取的素材上跑（需 `pip install -r requirements-dev.txt`） |

## 注意

- `config.json`（账号密码）、`login.json`（登录 cookie）、`log.log` 均已加入 `.gitignore`，**请勿提交到仓库**
- 换账号使用时必须删除 `login.json`（cookie 优先级高于账号密码），或由程序依据 `login_account.txt` 自动识别
- 本项目仅供个人学习研究，请勿用于违反学校管理规定的用途
