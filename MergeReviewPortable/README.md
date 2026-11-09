# Merge Review Portable

```text
Merge Review Portable — 局域网合并冲突确认与处理

依赖：Python 3.10+、Git。无第三方 Python 包。Windows / macOS / Linux 均可用。

1. 在目标仓库自行发起 git merge，保留未解决冲突，不要切分支。
2. python merge_review.py capture --repo "目标仓库" --out "sessions"
   输出独立会话目录和 review.md 检查文档。默认读取 HEAD、MERGE_HEAD 和真实索引冲突。
   可选 --ours-label 名称 --theirs-label 名称。
   若历史是 squash / 每日同步，可显式加 --replay-base 提交号，指定已同步基线；
   不指定时使用 Git 冲突索引的 base。工具不会猜测每日同步提交。
3. python merge_review.py serve --session "会话目录" --host 0.0.0.0 --port 8767
   用该主机的局域网 IP:8767 访问；页面内填写姓名并选择，导出 CSV。
   CSV 中 both / 未选择的行只留 base（当前分支基底）、replay_from..replay_to 和要重放的
   replay_commits（触及该文件的合入分支提交），可直接交给新 AI 会话重放。
   Windows 可用 Start.ps1 -RepoPath "仓库" 或 -SessionPath "会话目录" 后台启动。
   防火墙被阻止时，管理员执行 Allow-Lan.ps1 -Port 8767；仅开放 LocalSubnet。
4. python merge_review.py plan --session "会话目录" --csv "导出的CSV"
   也可省略 --csv，读取本会话网页保存的最新选择。
   pending / 未列出的文件默认 both。plan 不改仓库。
5. python merge_review.py apply --session "会话目录"
   先检查提交、索引和工作区仍与采集时一致，再备份，执行明确选边和干净重放。
   ours / theirs 是当前分支 / 合入分支的固定版本，与分支实际叫什么无关。
   both = 先采用 ours 为基底，再重放 theirs 的增量；不是拼接两份文件。
   有重叠语义、二进制、删除/重命名等情况时保留 ours 基底，保留未解决索引，
   生成 manual-replay.md 和 handoff.txt；退出码 2 表示仍有人工整合项。
6. 将 handoff.txt 和会话目录交给本地 AI 或开发者，按项目约定完成手工重放。
   完成并审核后：python merge_review.py finish --session "会话目录" --confirm-manual
   finish 检查冲突标记和 JSON/XML 格式，只暂存人工清单内的文件。
   无变化的基底需要逐文件显式 --accept-baseline "相对路径"，禁止一键假完成。
7. python merge_review.py verify --session "会话目录"
   查看 report.md、report.json。按项目执行编译/运行测试，再自行 commit / push。
   提交前如需撤回工具处理：python merge_review.py restore --session "会话目录" --confirm-restore
   只恢复本会话路径与原冲突索引；若文件又被修改则拒绝覆盖，备份仍可人工取回。

网页每个文件只留最后一次选择、修改人、时间；未选择置顶，按文件夹/文件名排序。
网页只保存选择，不执行 Git，不提供任意仓库文件下载。姓名是自行填写，未做登录。
会话包含代码差异和备份；只分发给应有权限的同事。它不是公网托管服务。
工具不 fetch/pull，不发起 merge，不 commit/push，不运行项目自定义构建或钩子绕过。
所有写入有会话内备份；不支持 rebase/cherry-pick/octopus merge、symlink/submodule 自动解决。
应用中断时不会重复覆盖：查看 apply-state.json 和 backup/；修复仓库后重新采集新会话。

复制整个工程即可复用；每次 capture 新建目录，不携带上一轮意见。不要把 sessions/
放入共享 Git。打包命令：python merge_review.py pack --out "MergeReviewPortable.zip"。

```
