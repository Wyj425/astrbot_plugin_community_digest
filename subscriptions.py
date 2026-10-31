"""Canonical topic library and independent group/private subscriptions."""
import re
import unicodedata

from .planning import clean, digest


def normalized(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', clean(value, 80))).strip().casefold()


def library(config):
    tags, lookup = {}, {}
    for entry in config.get('tag_library', []):
        name = clean(entry.get('name', ''), 40)
        if not name:
            continue
        key = normalized(name)
        if key in tags:
            raise ValueError('标签名称重复：' + name)
        words = list(dict.fromkeys(clean(w, 40) for w in entry.get('search_keywords', []) if clean(w, 40)))[:8] or [name]
        tags[key] = dict(id=key, name=name, keywords=words)
        for alias in [name] + entry.get('aliases', []):
            alias = normalized(alias)
            if alias in lookup and lookup[alias] != key:
                raise ValueError('标签别名冲突：' + alias)
            lookup[alias] = key
    return tags, lookup


def resolve_tags(config, values):
    tags, lookup = library(config)
    result = {}
    for value in values:
        key = normalized(value)
        if not key:
            continue
        canonical = lookup.get(key, key)
        tag = dict(tags.get(canonical, dict(id=canonical, name=clean(value, 40), keywords=[clean(value, 40)])))
        tag['cache_key']='tagdata:'+digest([tag['id'],tag['keywords']])
        result[canonical] = tag
    return list(result.values())[:24]


def tag_profile(config, target, tag):
    # Rating is per canonical topic, never per recipient or full subscription set.
    key = 'tag:' + digest({'tag':tag['id'], 'queries':tag['keywords'],
                          'provider':config.get('provider_id'), 'rubric':3})
    return {**target, 'key':key, 'group_id':'tag:' + tag['id'],
            'keywords':[tag['name']], 'tags':[tag], 'tag_id':tag['id'],
            'cache_key':'tagdata:'+digest([tag['id'],tag['keywords']])}


def search_key(tag, source, max_age):
    # Different source subscriptions reuse each overlapping platform independently.
    return 'search:' + digest([tag['id'], tag['keywords'], source, float(max_age)])


def target_umo(config, p):
    return p.get('umo') or f"{config.get('platform_id','')}:GroupMessage:{p['group_id']}"
