# 山东协和学院晚查寝自动签到程序

自动打开浏览器完成「智能查寝」签到：自动登录、注入签到位置、识别并点击签到按钮、
记录签到结果。滑块人机验证由**本地图像算法**完成——不联网、不调用任何第三方 API。

## 功能

- **自动登录**：账号密码保存在本地 `config.json`（支持明文或 base64），登录状态缓存到 `login.json`（约一周有效，免重复登录）
- **定位注入**：通过浏览器 geolocation 注入签到位置，经纬度可在 `config.json` 修改
- **签到按钮识别**：页面同时存在「签到」「晚归签到」等多个按钮时也能正确选择并点击
- **滑块验证自动通过**：读取验证码画布像素 → 用零均值归一化相关（NCC）定位拼图缺口（精确到 1 像素）→ 拟人轨迹一次拖到位；失败自动刷新重试
- **结果留痕**：`log.log` 追加记录、保存签到结果截图
- **可打包 exe**：PyInstaller 单文件打包，双击即用，无需装 Python

## 运行环境

- Windows 10 / 11，且装有 Microsoft Edge（脚本用 `channel="msedge"` 调用系统 Edge，不额外下载浏览器）
- 源码运行需要 Python 3.9+

## 安装依赖

```powershell
python -m venv .venv
.venv\Scripts\pip install playwright win10toast pywin32 numpy "setuptools<81"
```

> `setuptools<81` 是必需的：`win10toast` 依赖 `pkg_resources`，而 setuptools 81+ 已移除它。

## 使用

1. 复制 `config.example.json` 为 `config.json`，填入自己的账号密码：

   ```json
   {
       "username": "你的学号",
       "password": "你的密码",
       "location": { "longitude": "117.261944", "latitude": "36.739722" },
       "monkey_verify": "true",
       "expired": { "year": 2026, "month": 1, "day": 1 }
   }
   ```

   - `monkey_verify`：`"true"` 自动过滑块（推荐）；`"false"` 弹通知由人工滑动
   - 不填账号密码也能用：运行时在弹出的浏览器里手动登录一次

2. 运行：

   ```powershell
   .venv\Scripts\python autoCheckIn.py
   ```

   或者在 [Releases](https://github.com/chenfeng36/sdxiehe-checkin-auto/releases) 里下载打包好的
   `autoCheckIn.exe`（说明文档为 `instructions.txt`）双击运行；下载后可自行重命名为「自动签到.exe」。

   > GitHub 不支持中文附件名（会被自动替换成 `default`），因此 Release 附件使用 ASCII 文件名。

3. 查看结果：`log.log` 记录每次签到结果，`签到成功.png` 是签到后的页面截图。

**安全试运行**（只检测页面状态，不会点击签到）：

```powershell
$env:CHECKIN_DRY_RUN=1; python autoCheckIn.py
```

## 打包 exe

```powershell
.venv\Scripts\pyinstaller.exe --noconfirm --onefile --console --name "自动签到" `
    --collect-all playwright --collect-all win10toast `
    --hidden-import win32console --hidden-import win32timezone --hidden-import pkg_resources `
    autoCheckIn.py
```

## 调试脚本

| 脚本 | 用途 |
| --- | --- |
| `captcha_debug.py` | 抓取滑块验证码的原图、DOM 结构与几何信息（用于开发识别算法） |
| `captcha_solve_test.py` | 真实登录页上实测滑块识别成功率 |
| `captcha_match_test4.py` | 缺口定位算法（NCC）离线版本，可在已抓取的素材上跑 |

## 注意

- `config.json`（账号密码）、`login.json`（登录 cookie）、`log.log` 均已加入 `.gitignore`，**请勿提交到仓库**
- 换账号使用时必须删除 `login.json`（cookie 优先级高于账号密码），或由程序依据 `login_account.txt` 自动识别
- 本项目仅供个人学习研究，请勿用于违反学校管理规定的用途
