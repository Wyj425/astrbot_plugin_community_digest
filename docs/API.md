# 插件间接口 v1

消费者通过 AstrBot 的 `context.get_registered_star(name)` 获得已启用插件的 `star_cls`，检查 `api_version == 1` 后调用。每次调用重新查找，避免插件重载后持有失效实例。不得导入其他插件的私有文件或读取其凭据。

```python
meta = context.get_registered_star("astrbot_plugin_bili_reader")
if not meta or not meta.activated:
    raise RuntimeError("请安装并启用 B站内容读取")
provider = meta.star_cls
if provider.api_version != 1:
    raise RuntimeError("接口版本不兼容")
items = await provider.search(["英雄联盟"], max_age=7)
detail = await provider.read(items[0]["url"], images=False)
```

平台统一接口 `search(words, max_age=7)`、`read(url, images=True)`、`status()`。返回普通字典和列表；搜索项含 id/source/title/url/cover/description/published_at/queries。详情含 body/evidence/evidence_kind/comments/image_urls/images/images_loaded/images_total/comment_scope。缺少或不可读的字段不作推测。

`images` 是供模型输入的数据URL，日志和管理API不返回完整图像。`images=False` 的工具调用不会附加图片到模型，但小黑盒底层详情缓存可能已包含图片。`evidence_kind` 区分帖子正文、笔记正文、视频字幕节选和标题与简介。小红书的时间筛选受后端支持范围限制；最终候选年龄须由消费者再次核查。

卡片服务 `await card.sign(news)`，news 含 title/desc/preview/jumpUrl/tag，返回 `_signed_json` 与 app。消费者原样发送 `_signed_json`。原生音乐 `await card.native_music(song)` 为实验性接口，失败时应显式退回图文卡或普通链接。

依赖缺失/停用/接口版本不匹配会抛出 RuntimeError；远端失败有超时、并发上限和短暂失败冷却。业务插件不得把签名/发送不确定状态自动当作可重试，否则可能重复发送。

管理端接口通过 AstrBot 已认证插件 API：`status` GET、`login` POST、`verify` POST、`inspect` POST（参数 url/images，仅解析，不向QQ发送）。QQ卡片插件仅有 status。管理员登录入口不是模型工具。
