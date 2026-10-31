import asyncio
import json
from urllib.parse import urlsplit, parse_qs

from astrbot.api import logger


def number(value):
    if value is None:
        return '暂不可用'
    n = int(value)
    return f'{n / 10000:.1f}万' if n >= 10000 else str(n)


def image_url(value):
    value = str(value or '')
    if value.startswith('//'):
        value = 'https:' + value
    if value.startswith('http://'):
        value = 'https://' + value[7:]
    return value if value.startswith('https://') else ''


def link_card(item, description, cover, sender_id=None):
    apps = {
        'B站': (100951776, '哔哩哔哩', 'https://open.gtimg.cn/open/app_icon/00/95/17/76/100951776_100_m.png'),
        '小红书': (100507190, '小红书', 'https://open.gtimg.cn/open/app_icon/00/50/71/90/100507190_100_m.png'),
        '小黑盒': (1105910806, '小黑盒', 'https://open.gtimg.cn/open/app_icon/05/91/08/06/1105910806_100_m.png'),
    }
    appid, tag, icon = apps[item['source']]
    news = {'app_type': 1, 'appid': appid, 'desc': description[:220],
            'jumpUrl': item['url'], 'preview': image_url(cover), 'tag': tag,
            'tagIcon': icon, 'title': item['title']}
    # Local metadata for rendering or requesting a new card; never sent as JSON.
    return {'meta': {'news': news}}


async def build_card(bot, item, root, reason='', preview_only=False, signer=None, source_in_title=False):
    from .main import clean
    cover = image_url(item.get('cover'))
    description = clean(item.get('description', ''), 180) or reason or '点击查看原内容'
    if item['source']=='B站' and item.get('bili_info'):
        info=item['bili_info'];owner=info.get('owner',{});stat=info.get('stat',{})
        cover=image_url(info.get('pic')) or cover
        description=(f"UP主：{clean(owner.get('name',''),30)} · {number(item.get('bili_fans'))}粉丝\n"
            f"{number(stat.get('view'))}播放 · {number(stat.get('like'))}点赞 · {number(stat.get('danmaku'))}弹幕")
    if preview_only:
        return link_card(item, description, cover)
    metadata = link_card(item, description, cover)
    if source_in_title and item['source'] == '小红书':
        metadata['meta']['news']['title'] = '【' + item['source'] + '】' + metadata['meta']['news']['title']
    if signer is None: raise RuntimeError('请安装并启用 QQ卡片签名 插件')
    payload = await signer.sign(metadata['meta']['news'])
    # Retain the last generated card per source for checking the actual client template.
    (root / ('last-card-' + {'B站': 'bili', '小红书': 'xhs', '小黑盒': 'heybox'}[item['source']] + '.json')).write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return payload


async def send_card(bot, target, payload):
    if not isinstance(payload.get('_signed_json'), str):
        raise ValueError('a newly generated signed card is required')
    parts = target.split(':')
    if len(parts) != 3 or not parts[2].isdigit():
        raise ValueError('invalid card target')
    if parts[1] == 'GroupMessage':
        destination = {'group_id': int(parts[2])}
        action = 'send_group_msg'
    elif parts[1] == 'FriendMessage':
        destination = {'user_id': int(parts[2])}
        action = 'send_private_msg'
    else:
        raise ValueError('unsupported card destination')
    result = await bot.call_action(action, **destination,
        message=[{'type': 'json', 'data': {'data': payload['_signed_json']}}])
    if not isinstance(result, dict) or result.get('message_id') is None:
        raise RuntimeError('card send returned no message_id; do not retry')
    logger.info('[community_digest] card accepted message_id=%s app=%s', result['message_id'], payload['app'])
    return result
