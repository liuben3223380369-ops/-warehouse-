# 打包成 Windows exe

## 先说结论

**能打包，但不能在这里帮你打。**

PyInstaller **不支持交叉编译** —— 想要 Windows 的 `.exe`，必须在 Windows 电脑上跑打包命令。
在 Linux/Mac/手机 Termux 上只能打出对应平台的可执行文件，打不出 exe。

好消息是：**打包要用的东西我都准备好了，你在 Windows 上双击一个 bat 就能出 exe。**

> 我已经在 Linux 上用同一份配置完整跑通了打包 → 启动 → 录单 → 落盘 → 重启验证的全流程，
> 配置是验过的，你在 Windows 上照做即可（详见文末"我验证过什么"）。

---

## Windows 上三步出 exe

### 1. 装 Python（只需一次）

去 https://www.python.org/downloads/ 下载，安装时**务必勾选 `Add Python to PATH`**。

### 2. 把整个 `warehouse` 文件夹拷到 Windows 上

### 3. 双击 `打包成exe.bat`

等着就行，1~3 分钟。完成后：

```
dist\仓库管理系统.exe     ← 就是这个
```

---

## 用的时候注意

| 事项 | 说明 |
|---|---|
| **数据位置** | 保存在 **exe 旁边的 `warehouse.db`**，换电脑/重装时把这个文件拷走 |
| **自动备份** | 同目录会生成 `.bak` 文件，出问题改名回 `warehouse.db` 即可还原 |
| **怎么停** | 直接关掉黑窗口，或按 `Ctrl+C` |
| **黑窗口** | 故意保留的：能看到访问地址，也方便报错时看原因。别嫌它丑 |
| **端口占用** | 默认 8080，被占用会提示。想换端口：`仓库管理系统.exe 9000` |

---

## 常见问题

**杀毒软件报毒 / 直接删了 exe？**
PyInstaller 打包的程序常被误报。把它加入杀软白名单（信任/允许）即可。
这是所有 PyInstaller 程序的通病，不是病毒。

**双击没反应 / 一闪而过？**
在 exe 所在文件夹里按住 `Shift` + 右键 → "在此处打开 PowerShell"，输入 `.\仓库管理系统.exe` 回车，
就能看到报错信息。

**提示端口被占用？**
换端口启动：`仓库管理系统.exe 9000`

**exe 有多大？**
大概 30~50 MB。因为把 Python 解释器、Flask、Excel 读写库全塞进去了，这是正常的。

**想换成自己的图标？**
准备一个 `app.ico`，把 `warehouse.spec` 最后那行 `icon=None` 改成 `icon='app.ico'`，重新打包。

**想不显示黑窗口？**
把 `warehouse.spec` 里的 `console=True` 改成 `console=False`。
但那样就看不到访问地址、报错也看不见，一般不建议。

---

## 手动打包（不用 bat）

```bat
pip install -r requirements.txt pyinstaller
pyinstaller warehouse.spec --noconfirm --clean
```

---

## 打包相关的文件

| 文件 | 作用 |
|---|---|
| `warehouse.spec` | PyInstaller 配置：要打包哪些文件、哪些模块 |
| `打包成exe.bat` | Windows 一键打包脚本 |
| `requirements.txt` | 依赖清单（Flask / openpyxl / xlrd / pyinstaller） |

代码里为打包做的适配：
- 模板目录、种子数据在 exe 里从临时解包目录读取（`db.res_dir()`）
- **数据库放在 exe 旁边**（`db.app_dir()`），不会每次运行都变——这点很关键，
  否则数据会写进临时目录，关掉程序就没了
- exe 启动后自动打开浏览器
- 关窗口 / 报错时给提示，不会无声无息退出

---

## 我验证过什么

在 Linux 上用**同一份 `warehouse.spec`** 完整跑了一遍：

- 打包成功，产出可执行文件
- 启动正常，打印库概况和访问地址
- 12 个页面 + 2 个导出接口全部 200（模板确实打进去了）
- 录单（入库 100/2 件、出库 30/1 件）→ 库存 70，件数换算正确
- **关掉程序重启，数据还在**（证明数据库路径没写错）

Windows 上的流程完全一样，区别只是产物是 `.exe`。
