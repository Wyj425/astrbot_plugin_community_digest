"""Resolve live plugin services on each call; no imports of another plugin's files."""
NAMES={'B站':'astrbot_plugin_bili_reader','小红书':'astrbot_plugin_xhs_reader','小黑盒':'astrbot_plugin_heybox_reader','card':'astrbot_plugin_qq_card'}
def service(context, source):
    name=NAMES[source]
    meta=context.get_registered_star(name)
    value=getattr(meta,'star_cls',None) if meta else None
    if not meta or not getattr(meta,'activated',False) or value is None:
        raise RuntimeError('请安装并启用依赖插件：'+name)
    if getattr(value,'api_version',None)!=1:raise RuntimeError('依赖插件接口版本不兼容：'+name)
    if not getattr(value,'config',{}).get('enabled',True):raise RuntimeError('依赖插件已关闭：'+name)
    return value
