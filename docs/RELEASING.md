# 发布检查

1. 为每个插件创建独立 GitHub 仓库，仓库名建议使用 astrbot_plugin_ 前缀；本目录内容放仓库根目录。
2. 将 metadata.yaml 的 author 和 repo 改为真实账号、真实地址；不要保留虚假地址。name 是稳定插件ID，升级不要随意更改。
3. 运行 Python 语法检查与本仓库 tests 中的离线测试；在测试 AstrBot 实例中安装依赖后验证扫码、读取和禁用依赖。不要用自动测试向真实群发消息。
4. 检查 README、requirements.txt、LICENSE、第三方声明、CHANGELOG、配置默认值；ZIP不得包含数据库、缓存、配置实例和凭据。
5. 插件ZIP须小于16MB。当前源码不包含运行时的图片/音频和浏览器二进制。
6. 推送并打版本标签后，在 https://cloud.astrbot.app 插件发布页面提交。GitHub绑定不等于已创建仓库，提交也不等于审核通过。

官方发布流程：https://docs.astrbot.app/dev/star/plugin-publish.html
