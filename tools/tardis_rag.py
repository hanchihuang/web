import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TardisKnowledgeChunk:
    title: str
    content: str
    keywords: tuple[str, ...]


TARDIS_KNOWLEDGE_BASE: tuple[TardisKnowledgeChunk, ...] = (
    TardisKnowledgeChunk(
        title='TARDIS 适合什么需求',
        content='TARDIS 适合做撮合、盘口、成交级别研究，也适合研究、回测、信号验证和生产环境数据补全。',
        keywords=('适合', '需求', '高频', '回测', '研究', '盘口', '成交', '实时'),
    ),
    TardisKnowledgeChunk(
        title='可提供的数据类型',
        content='当前页面写明可提供三个 Tardis API：期权、期货、现货；历史数据和实时数据都可覆盖，各交易所数据都有。',
        keywords=('期权', '期货', '现货', '历史', '实时', '交易所', 'api'),
    ),
    TardisKnowledgeChunk(
        title='高档 API 能力',
        content='页面写的是 Solo 每月 1200 美元档位能力，对应年费价值约十万人民币，最高档位可直接批量下载。',
        keywords=('1200', '美元', 'solo', '年费', '十万', '批量', '下载'),
    ),
    TardisKnowledgeChunk(
        title='单次下载少量数据价格',
        content='单次下载少量数据是 100 元，适合只拿少量样本或做一次性验证。',
        keywords=('100元', '单次', '少量', '样本', '一次性', '价格'),
    ),
    TardisKnowledgeChunk(
        title='定制链接日更价格',
        content='定制链接每日更新是 100 元每月起，适合少量数据日更，不再自己维护下载链路。',
        keywords=('日更', '每日更新', '100元/月', '链接', '定制', '月付'),
    ),
    TardisKnowledgeChunk(
        title='整月租用高档 API 价格',
        content='整月租用高档 API 是 1500 元每月，适合大量历史数据需求或团队型下载。',
        keywords=('1500', '包月', '整月', '团队', '大量', '历史数据'),
    ),
    TardisKnowledgeChunk(
        title='Deribit 特价数据',
        content='Deribit 期权所有币种数据按年限打包：1T 一年 200 元，2T 两年 350 元，3T 三年 480 元。',
        keywords=('Deribit', '期权', '1T', '2T', '3T', '200', '350', '480'),
    ),
    TardisKnowledgeChunk(
        title='Binance 特价数据',
        content='Binance 合约 BTC 全历史约 2T 多数据 300 元；Binance 现货 BTC 全历史数据 180 元。',
        keywords=('Binance', 'BTC', '合约', '现货', '300', '180', '全历史'),
    ),
    TardisKnowledgeChunk(
        title='联系方式',
        content='需要下载数据可直接添加微信 15180066256；如需通过付款截图领取本人购买的 crypto 高频课程和代码，可再加 dreamsjtuai 或 a13479004101。',
        keywords=('联系', '微信', '15180066256', 'dreamsjtuai', 'a13479004101', '课程', '代码'),
    ),
)

TOKEN_RE = re.compile(r'[A-Za-z0-9_./+-]+|[\u4e00-\u9fff]')
INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    'delivery': (
        '发货形式', '发货方式', '交付形式', '交付方式', '数据怎么发给我', '数据你怎么发给我', '怎么发给我',
        '怎么给我数据', '怎么把数据给我', '数据怎么给我', '通过什么方式发我', '怎么下载', '下载链接',
        '怎么交付', '如何交付', '怎么发送', '怎么发数据',
    ),
    'price': (
        '价格', '多少钱', '怎么收费', '收费方式', '包月多少钱', '单次多少钱', '月租', '报价',
    ),
    'contact': (
        '联系方式', '怎么联系', '联系你们', '微信多少', '联系微信', '找谁', '怎么下单',
    ),
    'coverage': (
        '支持哪些数据', '数据类型', '覆盖哪些数据', '支持哪些交易所', '交易所覆盖', '有哪些数据',
    ),
    'realtime': (
        '实时数据', '历史数据', '实时还是历史', '是否实时', '能不能实时', '历史回放',
    ),
}

THEME_PRESETS: dict[str, dict[str, tuple[str, ...] | str]] = {
    'price': {
        'title': '价格与收费',
        'aliases': INTENT_ALIASES['price'],
        'keywords': ('价格', '收费', '报价', '包月', '单次', '月租'),
    },
    'delivery': {
        'title': '发货与交付',
        'aliases': INTENT_ALIASES['delivery'],
        'keywords': ('发货', '交付', '链接', '发送', '下载', '交付方式'),
    },
    'contact': {
        'title': '联系方式与下单',
        'aliases': INTENT_ALIASES['contact'],
        'keywords': ('联系', '微信', '下单', '付款', '沟通'),
    },
    'coverage': {
        'title': '数据覆盖范围',
        'aliases': INTENT_ALIASES['coverage'],
        'keywords': ('覆盖', '数据类型', '交易所', '期权', '期货', '现货'),
    },
    'realtime': {
        'title': '实时与历史支持',
        'aliases': INTENT_ALIASES['realtime'],
        'keywords': ('实时', '历史', '回放', '订阅', '日更'),
    },
    'deribit': {
        'title': 'Deribit 特价数据',
        'aliases': ('Deribit 怎么卖', 'Deribit 特价', 'Deribit 数据价格'),
        'keywords': ('Deribit', '期权', '1T', '2T', '3T'),
    },
    'binance': {
        'title': 'Binance 特价数据',
        'aliases': ('Binance 怎么卖', 'Binance 特价', 'Binance 数据价格'),
        'keywords': ('Binance', 'BTC', '现货', '合约', '全历史'),
    },
}
EXPLICIT_QA_RE = re.compile(
    r'(?:^|\n)\s*(?:Q|q|问|问题)\s*[:：]\s*(?P<question>.+?)'
    r'\s*(?:\n+\s*(?:A|a|答|回答)\s*[:：]\s*(?P<answer>.+?))'
    r'(?=(?:\n+\s*(?:Q|q|问|问题)\s*[:：])|\Z)',
    re.S,
)
NUMBER_TOKEN_RE = re.compile(r'[A-Za-z]{1,8}\d+[A-Za-z0-9]*|\d+(?:\.\d+)?(?:元|美元|月|年|T|GB|tb|TB|mb|MB)?')


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or '')]


def _expand_question_tokens(question: str) -> list[str]:
    cleaned = (question or '').strip().lower()
    tokens = _tokenize(cleaned)
    expanded_phrases = []
    for intent, aliases in INTENT_ALIASES.items():
        if any(alias.lower() in cleaned for alias in aliases):
            expanded_phrases.append(intent)
            expanded_phrases.extend(aliases)
    if expanded_phrases:
        tokens.extend(_tokenize(' '.join(expanded_phrases)))
    return tokens


def _score_chunk(question_tokens: list[str], chunk: TardisKnowledgeChunk) -> float:
    if not question_tokens:
        return 0.0
    content_tokens = _tokenize(f'{chunk.title} {chunk.content} {" ".join(chunk.keywords)}')
    if not content_tokens:
        return 0.0
    overlap = 0.0
    for token in question_tokens:
        if token in content_tokens:
            overlap += 1.6
        if token in {keyword.lower() for keyword in chunk.keywords}:
            overlap += 2.4
    diversity = len(set(content_tokens))
    return overlap / math.sqrt(max(diversity, 1))


def _split_blocks(text: str) -> list[str]:
    return [block.strip() for block in re.split(r'\n\s*\n+', text or '') if block.strip()]


def _split_sentences(text: str) -> list[str]:
    fragments = re.findall(r'[^。！？!?；;\n]+[。！？!?；;]?', text or '')
    return [fragment.strip(' \t-') for fragment in fragments if fragment.strip(' \t-')]


def _detect_themes(text: str) -> list[str]:
    cleaned = (text or '').strip().lower()
    themes: list[str] = []
    for theme, preset in THEME_PRESETS.items():
        aliases = preset['aliases']
        keywords = preset['keywords']
        if any(alias.lower() in cleaned for alias in aliases) or any(keyword.lower() in cleaned for keyword in keywords):
            themes.append(theme)
    return themes


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(normalized)
    return result


def _extract_keywords(text: str, matched_themes: list[str], extra_keywords: str = '') -> str:
    candidates: list[str] = []
    for theme in matched_themes:
        preset = THEME_PRESETS.get(theme) or {}
        candidates.extend(list(preset.get('keywords', ())))
    candidates.extend(NUMBER_TOKEN_RE.findall(text or ''))
    for token in ('TARDIS', 'Deribit', 'Binance', 'BTC', '期权', '期货', '现货', '实时', '历史', 'API', '微信', '链接'):
        if token.lower() in (text or '').lower():
            candidates.append(token)
    candidates.extend([item.strip() for item in (extra_keywords or '').replace('\n', ',').split(',') if item.strip()])
    return ','.join(_dedupe_keep_order(candidates)[:12])


def _infer_title(text: str, matched_themes: list[str]) -> str:
    if matched_themes:
        return str(THEME_PRESETS[matched_themes[0]]['title'])
    snippet = (text or '').replace('\n', ' ').strip()
    return snippet[:32] if len(snippet) > 32 else snippet


def _build_question_hint(text: str, matched_themes: list[str]) -> str:
    hints: list[str] = []
    for theme in matched_themes:
        hints.extend(list(THEME_PRESETS[theme]['aliases'])[:4])
    snippet = (text or '').replace('\n', ' ').strip()
    if snippet:
        hints.append(snippet[:48] if len(snippet) > 48 else snippet)
    return ','.join(_dedupe_keep_order(hints)[:8])


def _entry_from_text(answer: str, sort_order: int, extra_keywords: str = '', explicit_question: str = '') -> dict | None:
    cleaned_answer = (answer or '').strip()
    if len(cleaned_answer) < 6:
        return None
    theme_source = explicit_question or cleaned_answer
    matched_themes = _detect_themes(f'{explicit_question}\n{cleaned_answer}')
    title = explicit_question.strip() if explicit_question.strip() else _infer_title(theme_source, matched_themes)
    question_hint = _build_question_hint(explicit_question or cleaned_answer, matched_themes)
    keywords = _extract_keywords(f'{explicit_question}\n{cleaned_answer}', matched_themes, extra_keywords=extra_keywords)
    return {
        'title': title[:120],
        'question_hint': question_hint[:255],
        'answer': cleaned_answer,
        'keywords': keywords[:255],
        'sort_order': sort_order,
        'is_active': True,
    }


def extract_tardis_entries_from_text(text: str, *, start_sort: int = 100, extra_keywords: str = '') -> list[dict]:
    cleaned = (text or '').strip()
    if not cleaned:
        return []

    entries: list[dict] = []
    sort_order = start_sort
    explicit_pairs = list(EXPLICIT_QA_RE.finditer(cleaned))
    if explicit_pairs:
        for match in explicit_pairs:
            entry = _entry_from_text(
                answer=match.group('answer'),
                sort_order=sort_order,
                extra_keywords=extra_keywords,
                explicit_question=match.group('question'),
            )
            if entry:
                entries.append(entry)
                sort_order += 5
        return entries

    for block in _split_blocks(cleaned):
        sentences = _split_sentences(block)
        if not sentences:
            continue
        groups: list[dict] = []
        for sentence in sentences:
            matched_themes = _detect_themes(sentence)
            theme = matched_themes[0] if matched_themes else (groups[-1]['theme'] if groups else 'general')
            if groups and groups[-1]['theme'] == theme:
                groups[-1]['sentences'].append(sentence)
            else:
                groups.append({'theme': theme, 'sentences': [sentence]})

        for group in groups:
            answer = ' '.join(group['sentences']).strip()
            entry = _entry_from_text(
                answer=answer,
                sort_order=sort_order,
                extra_keywords=extra_keywords,
            )
            if entry:
                duplicate = next(
                    (
                        existing for existing in entries
                        if existing['answer'] == entry['answer'] or (
                            existing['title'] == entry['title'] and existing['keywords'] == entry['keywords']
                        )
                    ),
                    None,
                )
                if duplicate is None:
                    entries.append(entry)
                    sort_order += 5
    return entries


def build_dynamic_chunks(entries) -> list[TardisKnowledgeChunk]:
    chunks = []
    for entry in entries:
        keywords = tuple(
            token.strip()
            for token in f'{entry.keywords},{entry.question_hint}'.replace('\n', ',').split(',')
            if token.strip()
        )
        chunks.append(
            TardisKnowledgeChunk(
                title=entry.title,
                content=entry.answer,
                keywords=keywords,
            )
        )
    return chunks


def answer_tardis_question(question: str, extra_chunks=None) -> dict:
    cleaned = (question or '').strip()
    if not cleaned:
        return {
            'ok': False,
            'answer': '请直接问一个和 TARDIS 数据相关的问题，比如价格、数据覆盖、实时/历史支持、Deribit 或 Binance 特价、联系方式。',
            'references': [],
        }

    question_tokens = _expand_question_tokens(cleaned)
    all_chunks = list(extra_chunks or []) + list(TARDIS_KNOWLEDGE_BASE)
    scored = sorted(
        (
            (_score_chunk(question_tokens, chunk), chunk)
            for chunk in all_chunks
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    top_chunks = [chunk for score, chunk in scored if score > 0][:3]
    if not top_chunks:
        return {
            'ok': True,
            'answer': (
                '当前页面里我能明确回答的主题主要有：TARDIS 适合什么需求、支持哪些数据类型、三档价格、'
                'Deribit/Binance 特价数据，以及联系方式。你可以换一个更具体的问题继续问。'
            ),
            'references': [],
        }

    answer_lines = ['根据当前这篇 TARDIS 指南，能确认的信息如下：']
    references = []
    for chunk in top_chunks:
        answer_lines.append(f'1. {chunk.content}')
        references.append({'title': chunk.title, 'content': chunk.content})
    answer_lines.append('如果你要，我可以继续按“价格”“覆盖交易所”“实时还是历史”“怎么联系”这几个方向继续细问。')
    return {
        'ok': True,
        'answer': '\n'.join(answer_lines),
        'references': references,
    }
