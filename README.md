# 社区精选分享

适用 AstrBot **4.29.3 及以上**，Python 3.12；当前实际验证版本为 AstrBot 4.29.3。其他版本需自行验证兼容性。

- 标签共享检索、候选评分、缓存复用、按群/私聊独立订阅、随机或固定时间推送。
- 可参考 LivingMemory 的同会话近期兴趣；LivingMemory 为可选依赖，不修改其记忆。
- 每次只选一条合格内容，卡片后附人设短评或注明来源的热门评论。
- 本插件不持有平台 Cookie、读取服务 Token 或卡片签名 Token。
- 默认关闭定时推送，完成订阅和平台选择后再开启。私聊必须明确加入订阅，不自动推送全部好友。

## 安装

1. 本插件仓库：[Wyj425/astrbot_plugin_community_digest](https://github.com/Wyj425/astrbot_plugin_community_digest)，由 `Wyj425` 维护。
2. 在 AstrBot → 插件 → 从链接安装，填写 `https://github.com/Wyj425/astrbot_plugin_community_digest`；也可导入只包含本目录的 ZIP。不要上传整个工作目录。
3. AstrBot 按 `requirements.txt` 安装 Python 依赖。启用插件后，在插件配置页设置。
4. QQ卡片签名为卡片推送依赖；启用哪个内容平台，就安装并登录哪个平台读取插件。缺少或失败的平台跳过，其他平台可继续。完整三平台体验安装全部四个能力插件。

## 使用与登录

配置项、默认值和边界见 [配置说明](docs/CONFIGURATION.md)。首次安装请填写自己的 QQ 接入平台 ID（如有此项），以及在 AstrBot 中已创建的模型/Embedding 提供商 ID。已部署用户迁移时保留原配置，不用重新填写订阅和历史。

依赖安装：[QQ卡片签名](https://github.com/Wyj425/astrbot_plugin_qq_card)、[B站内容读取](https://github.com/Wyj425/astrbot_plugin_bili_reader)、[小红书内容读取](https://github.com/Wyj425/astrbot_plugin_xhs_reader)、[小黑盒内容读取](https://github.com/Wyj425/astrbot_plugin_heybox_reader)。只启用所需的平台即可；平台登录和签名 Token 在各自插件中配置。

## 数据、费用与卸载

插件数据保存在 AstrBot 的 `data/plugin_data/community_digest/`，配置位于 `data/config/community_digest_config.json`。扫码凭据不返回到模型、聊天或普通日志。平台搜索/详情缓存按公开内容共享，会话最近链接按完整会话标识隔离；重启清空内存缓存，候选库与发送记录等业务数据库仍持久化。

平台读取与签名本身不调用语言模型。正文、评论和配图进入聊天模型时会增加输入用量；社区评分、兴趣分析、人设短评及知识提取仍按各插件设置计费。没有无限抓取或无限评论翻页。

卸载插件不会自动清除平台账户的远程登录，也不应直接删除需要保留的数据。停用平台插件后，消费者会报告依赖不可用或跳过该平台。需要重新安装时恢复本插件目录及配置；平台失效需重新扫码。

## 开发、测试与发布

- [接口说明](docs/API.md)：协议版本、调用方式、错误与限制。
- [发布检查](docs/RELEASING.md)：独立仓库、元数据、测试和隐私检查。
- [更新记录](CHANGELOG.md)。许可证为 MIT；第三方组件以各自许可证为准。

本项目当前提供独立发布源码，尚未声明已被插件市场收录。切勿把真实 Token、Cookie、群号订阅、私聊记录、数据库、登录二维码或调试响应上传。
