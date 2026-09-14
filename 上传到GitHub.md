# 上传到 GitHub

## 能不能让我帮你上传？

**不能**，有两个硬原因：

1. **没有你的账号凭据**。往 GitHub 推代码需要用户名 + Personal Access Token，
   这类密码我不会也不应该持有——你也不该把它发给任何 AI 或陌生脚本。
2. **运行环境连不上 GitHub**。当前沙盒到 `github.com` 的请求被拦截（403），
   即使有 token 也推不上去。

**但我已经把能做的都做好了**：本地 git 仓库已初始化、首次提交已完成、
`.gitignore` 已配好（数据库和备份不会误传）、自动打包 exe 的流程已写好。
你只需要**跑一条命令**。

---

## 你要做的（约 3 分钟）

### 第 1 步：在 GitHub 上建空仓库

打开 https://github.com/new

- Repository name 填 `warehouse`（或你喜欢的名字）
- 选 **Public** 或 **Private** 都行
- **不要**勾选 "Add a README file" / ".gitignore" / "license"，保持空仓库
- 点 Create repository

> 仓库名如果不用 `warehouse`，就把下面脚本里的地址改一下。

### 第 2 步：生成 Token（用来代替密码）

打开 https://github.com/settings/tokens

- 点 **Generate new token → Generate new token (classic)**
- Note 随便填，比如 `warehouse-upload`
- Expiration 选 90 days 或 No expiration
- **勾选 `repo`**（这一项打勾就够了，它包含子项）
- 拉到最底点 Generate token
- **立刻复制那串 `ghp_` 开头的字符**，关掉页面就再也看不到了

### 第 3 步：推送

在项目目录里执行：

```bash
bash 上传到GitHub.sh
```

提示输入时：
- **Username**：`liuben3223380369`
- **Password**：**粘贴刚才的 token**（不是登录密码！屏幕不显示，粘完直接回车）

看到 `推送完成！` 就好了。

> Mac/Linux 直接跑上面的命令。Windows 的话在 Git Bash 里跑，
> 或者用 GitHub Desktop 打开这个文件夹点 Publish。

---

## 常见问题

**提示 `repository not found`**
仓库没建，或者名字对不上。去 https://github.com/liuben3223380369?tab=repositories
确认仓库确实存在、名字和脚本里的一致。

**提示 `Authentication failed`**
用的是登录密码而不是 token。GitHub 从 2021 年起不再接受密码，必须用 token。

**提示 ` Permission denied (publickey)`**
远程地址是 SSH 形式但没配密钥。执行
`git remote set-url origin https://github.com/liuben3223380369/仓库管理系统.git`
换回 HTTPS 再推。

**担心数据泄露？**
不会。`.gitignore` 已经排除了 `warehouse.db` 和所有 `.bak`，
**你的出入库数据不会上传**，只上传程序代码。

**上传后想更新？**
改完代码再跑一次 `bash 上传到GitHub.sh` 即可，它会自动提交并推送。

---

## 额外福利：让 GitHub 自动帮你打包 exe

前面说过，Windows 的 exe 必须在 Windows 上打。既然代码已经上 GitHub，
可以让它官方的 Windows 机器免费帮你打：

```bash
git tag v1.0
git push origin v1.0
```

然后去仓库页面：

- **Actions** 标签页 → 看到打包任务在跑，等几分钟
- **Releases** 页面 → 下载 `仓库管理系统-windows.zip`

解压双击 `仓库管理系统.exe` 就能用，完全不用自己装 Python。

以后每次更新，打个新标签（v1.1、v1.2……）就会自动打包新版本。

---

## 已经为你准备好的文件

| 文件 | 作用 |
|---|---|
| `.gitignore` | 排除数据库、备份、打包产物，防止误传业务数据 |
| `.github/workflows/build-exe.yml` | GitHub 自动打 Windows exe 并发布 Release |
| `上传到GitHub.sh` | 一键提交 + 推送脚本 |
