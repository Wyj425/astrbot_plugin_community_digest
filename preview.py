"""Render a deterministic image preview and send it with a normal URL message."""
import asyncio
import base64
import io
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_DIR = Path('/usr/share/fonts/opentype/noto')


def font(size, bold=False):
    name = 'NotoSansCJK-Bold.ttc' if bold else 'NotoSansCJK-Regular.ttc'
    return ImageFont.truetype(str(FONT_DIR / name), size)


def wrap(text, face, width, max_lines):
    lines, line = [], ''
    text = ' '.join(str(text).split())
    for char in text:
        if face.getlength(line + char) > width and line:
            lines.append(line)
            line = char
        else:
            line += char
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while face.getlength(lines[-1] + '…') > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += '…'
    return lines


def render(news, image_bytes):
    source = news.get('tag', '精选内容')
    bili = source in ('B站', '哔哩哔哩')
    tint = {'B站':'#fb7299', '哔哩哔哩':'#fb7299', '小红书':'#ed304c', '小黑盒':'#242a34'}.get(source, '#457be6')
    title_font, body_font, label_font = font(40, True), font(27), font(26, True)
    title_width = 940 if bili else 666
    titles = wrap(news.get('title', ''), title_font, title_width, 3)
    descriptions = wrap(news.get('desc', ''), body_font, title_width, 4 if bili else 3)
    text_end = 128 + len(titles) * 59 + 22 + len(descriptions) * 43
    cover_y = text_end + 28
    bottom = cover_y + 528 + 38 if bili else max(text_end + 30, 362)
    height = bottom + 98
    canvas = Image.new('RGB', (1100, height), '#f2f3f7')
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((20, 20, 1080, height-20), radius=28, fill='white')
    d.rounded_rectangle((62, 60, 73, 99), radius=5, fill=tint)
    d.text((91, 60), source, font=label_font, fill=tint)
    y = 128
    for line in titles:
        d.text((62, y), line, font=title_font, fill='#1b202a')
        y += 59
    y += 22
    for line in descriptions:
        d.text((62, y), line, font=body_font, fill='#68717e')
        y += 43
    try:
        cover = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert('RGB')
        box = (62, cover_y, 1038, cover_y+528) if bili else (785, 128, 1038, 362)
        size = (box[2]-box[0], box[3]-box[1])
        fitted = ImageOps.fit(cover, size)
        mask = Image.new('L', size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=16, fill=255)
        canvas.paste(fitted, box[:2], mask)
    except Exception:
        raise ValueError('cover cannot be decoded')
    d.line((62, bottom, 1038, bottom), fill='#edf0f4', width=2)
    d.text((62, bottom+24), '原文链接见消息下方', font=font(24), fill='#89909a')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M')
    d.text((866, bottom+24), stamp, font=font(22), fill='#a2a8b2')
    buf = io.BytesIO()
    canvas.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


async def make_preview(payload, root):
    news = payload['meta']['news']
    url = news['preview']
    host = urlsplit(url).hostname or ''
    if urlsplit(url).scheme != 'https' or not any(host == h or host.endswith('.'+h)
        for h in ['hdslb.com', 'xhscdn.com', 'max-c.com', 'ugcimg.cn', 'qpic.cn']):
        raise ValueError('unexpected cover host')
    referer = 'https://www.xiaohongshu.com/' if host.endswith('.xhscdn.com') else 'https://www.bilibili.com/' if host.endswith('.hdslb.com') else 'https://www.xiaoheihe.cn/'
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        async with client.stream('GET', url, headers={'User-Agent':'Mozilla/5.0', 'Referer':referer}) as response:
            response.raise_for_status()
            chunks, count = [], 0
            async for data in response.aiter_bytes():
                count += len(data)
                if count > 10 * 1024 * 1024:
                    raise ValueError('cover too large')
                chunks.append(data)
    png = await asyncio.to_thread(render, news, b''.join(chunks))
    path = root / ('preview-' + str(news.get('appid', 'content')) + '.png')
    path.write_bytes(png)
    return png


async def send_preview(bot, target, payload, root):
    png = await make_preview(payload, root)
    category, identity = target.split(':')[1:]
    if not identity.isdigit() or category not in ('FriendMessage', 'GroupMessage'):
        raise ValueError('invalid preview destination')
    destination = {'user_id' if category == 'FriendMessage' else 'group_id': int(identity)}
    news = payload['meta']['news']
    result = await bot.call_action('send_private_msg' if category == 'FriendMessage' else 'send_group_msg',
        **destination, message=[
            {'type':'image','data':{'file':'base64://'+base64.b64encode(png).decode()}},
            {'type':'text','data':{'text':'\n'+news['jumpUrl']}}
        ])
    if not isinstance(result, dict) or result.get('message_id') is None:
        raise RuntimeError('no preview message_id; do not retry')
    return result
