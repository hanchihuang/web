import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TushareKnowledgeChunk:
    title: str
    content: str
    keywords: tuple[str, ...]


TUSHARE_KNOWLEDGE_BASE: tuple[TushareKnowledgeChunk, ...] = (
    TushareKnowledgeChunk(
        title='Tushare 适合什么场景',
        content='Tushare 更适合 A 股研究、事件驱动、舆情辅助、港美股补充、全球指数同步和中低频研究所需的数据补全。',
        keywords=('A股', '研究', '舆情', '港股', '美股', '全球指数', '中低频'),
    ),
    TushareKnowledgeChunk(
        title='当前鉴权方式',
        content='这个页面只支持 API Key，不再提供网页登录拿权限。只有超级管理员生成并发放的 API Key 才能访问站内 /tushare/ 转接接口。',
        keywords=('API Key', '鉴权', '网页登录取消', '超级管理员', '权限'),
    ),
    TushareKnowledgeChunk(
        title='分钟数据边界',
        content='当前站内 replay 只开放分钟数据以外的接口，不开放 /tushare/minute/*。分钟类仍应走原生 Tushare token，并遵守原生限频。',
        keywords=('分钟', 'minute', 'token', '限频', '原生', '不开放'),
    ),
    TushareKnowledgeChunk(
        title='可下载数据范围',
        content='当前说明页写明可覆盖互动易问答、筹码分布、股票、融资融券、新闻、期货、期权、港股、美股、外汇、全球指数、主连/连续合约、指数、ETF 和部分其他类型数据。',
        keywords=('互动易', '筹码分布', '股票', '融资融券', '新闻', '期货', '期权', '港股', '美股', '外汇', '全球指数', 'ETF'),
    ),
    TushareKnowledgeChunk(
        title='交付内容',
        content='默认交付两类凭证：1 个原生 Tushare token，1 个站内 replay API Key。站内这层转接按 API Key 管理，不再按网页登录态放行。',
        keywords=('交付', 'token', 'API Key', '凭证', '发货'),
    ),
    TushareKnowledgeChunk(
        title='保存周期',
        content='不同数据类型会按频率和查询参数自适应保留周期。新闻通常约 15 分钟，日频类通常保留到北京时间当日 24:00，历史日期查询通常保留 3 到 7 天，基础资料类通常保留 7 天，交易日历类通常保留 30 天。',
        keywords=('保存周期', '缓存', '15分钟', '日频', '3到7天', '30天'),
    ),
    TushareKnowledgeChunk(
        title='目录与调用方式',
        content='可通过 /tushare/pro/<api_name> 访问非分钟数据，完整目录可看 /tushare/pro/catalog 或前端目录页。',
        keywords=('catalog', 'pro', 'api_name', '目录', '示例参数'),
    ),
    TushareKnowledgeChunk(
        title='联系与销售口径',
        content='如闲鱼链接失效，可联系微信 dreamsjtuai。当前页面销售口径到期日为 2026-12-29。',
        keywords=('联系', '微信', 'dreamsjtuai', '2026-12-29'),
    ),
)

TOKEN_RE = re.compile(r'[A-Za-z0-9_./+-]+|[\u4e00-\u9fff]')
NUMBER_TOKEN_RE = re.compile(r'[A-Za-z]{1,12}\d+[A-Za-z0-9_./-]*|\d+(?:\.\d+)?(?:元|美元|月|年|天|GB|T|次|分钟)?')
INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    'auth': ('怎么开通', '如何开通', '怎么拿权限', 'API Key', '鉴权', '登录', '登录态'),
    'minute': ('分钟数据', 'minute', '分钟线', '分钟接口', '分钟能不能拿', '分钟开放吗'),
    'coverage': ('支持哪些数据', '能拿什么数据', '覆盖范围', '有哪些数据', '支持哪些接口'),
    'delivery': ('发货形式', '怎么发给我', '交付形式', '交付内容', '给我什么凭证'),
    'retention': ('保存多久', '保留多久', '缓存多久', '更新频率', '多久更新'),
    'catalog': ('目录', 'catalog', '参数示例', '怎么调用', 'pro 接口'),
    'contact': ('怎么联系', '联系方式', '微信多少', '闲鱼失效怎么办'),
}
THEME_PRESETS: dict[str, dict[str, tuple[str, ...] | str]] = {
    'auth': {'title': '权限与鉴权', 'aliases': INTENT_ALIASES['auth'], 'keywords': ('API Key', '鉴权', '登录', '权限', '开通')},
    'minute': {'title': '分钟数据边界', 'aliases': INTENT_ALIASES['minute'], 'keywords': ('分钟', 'minute', 'token', '限频')},
    'coverage': {'title': '数据覆盖范围', 'aliases': INTENT_ALIASES['coverage'], 'keywords': ('数据', '接口', 'A股', '港股', '美股', '期货', '期权', 'ETF')},
    'delivery': {'title': '交付与发货', 'aliases': INTENT_ALIASES['delivery'], 'keywords': ('交付', 'token', 'API Key', '凭证', '发货')},
    'retention': {'title': '保存周期与更新', 'aliases': INTENT_ALIASES['retention'], 'keywords': ('保存', '保留', '缓存', '更新', '15分钟', '30天')},
    'catalog': {'title': '目录与调用示例', 'aliases': INTENT_ALIASES['catalog'], 'keywords': ('catalog', 'pro', '调用', '参数', 'api_name')},
    'contact': {'title': '联系方式', 'aliases': INTENT_ALIASES['contact'], 'keywords': ('联系', '微信', 'dreamsjtuai')},
}
EXPLICIT_QA_RE = re.compile(
    r'(?:^|\n)\s*(?:Q|q|问|问题)\s*[:：]\s*(?P<question>.+?)'
    r'\s*(?:\n+\s*(?:A|a|答|回答)\s*[:：]\s*(?P<answer>.+?))'
    r'(?=(?:\n+\s*(?:Q|q|问|问题)\s*[:：])|\Z)',
    re.S,
)


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


def _score_chunk(question_tokens: list[str], chunk: TushareKnowledgeChunk) -> float:
    if not question_tokens:
        return 0.0
    content_tokens = _tokenize(f'{chunk.title} {chunk.content} {" ".join(chunk.keywords)}')
    if not content_tokens:
        return 0.0
    overlap = 0.0
    keyword_tokens = {keyword.lower() for keyword in chunk.keywords}
    for token in question_tokens:
        if token in content_tokens:
            overlap += 1.6
        if token in keyword_tokens:
            overlap += 2.4
    return overlap / math.sqrt(max(len(set(content_tokens)), 1))


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
    for token in ('Tushare', 'API Key', 'token', 'A股', '港股', '美股', '期货', '期权', 'ETF', 'catalog', 'dreamsjtuai'):
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
    matched_themes = _detect_themes(f'{explicit_question}\n{cleaned_answer}')
    title = explicit_question.strip() if explicit_question.strip() else _infer_title(cleaned_answer, matched_themes)
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


def extract_tushare_entries_from_text(text: str, *, start_sort: int = 100, extra_keywords: str = '') -> list[dict]:
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
            entry = _entry_from_text(' '.join(group['sentences']).strip(), sort_order, extra_keywords=extra_keywords)
            if entry and not any(existing['answer'] == entry['answer'] for existing in entries):
                entries.append(entry)
                sort_order += 5
    return entries


def build_dynamic_chunks(entries) -> list[TushareKnowledgeChunk]:
    chunks = []
    for entry in entries:
        keywords = tuple(
            token.strip()
            for token in f'{entry.keywords},{entry.question_hint}'.replace('\n', ',').split(',')
            if token.strip()
        )
        chunks.append(TushareKnowledgeChunk(title=entry.title, content=entry.answer, keywords=keywords))
    return chunks


def answer_tushare_question(question: str, extra_chunks=None) -> dict:
    cleaned = (question or '').strip()
    if not cleaned:
        return {
            'ok': False,
            'answer': '请直接问一个和 Tushare 数据权限相关的问题，比如鉴权方式、分钟数据、可下载范围、交付形式、保存周期、目录调用或联系方式。',
            'references': [],
        }
    question_tokens = _expand_question_tokens(cleaned)
    all_chunks = list(extra_chunks or []) + list(TUSHARE_KNOWLEDGE_BASE)
    scored = sorted(((_score_chunk(question_tokens, chunk), chunk) for chunk in all_chunks), key=lambda item: item[0], reverse=True)
    top_chunks = [chunk for score, chunk in scored if score > 0][:3]
    if not top_chunks:
        return {
            'ok': True,
            'answer': '当前页面里我能明确回答的主题主要有：API Key 鉴权、分钟数据边界、可下载数据范围、交付内容、保存周期、目录调用和联系方式。你可以换个更具体的问题继续问。',
            'references': [],
        }
    answer_lines = ['根据当前这篇 Tushare 指南，能确认的信息如下：']
    references = []
    for chunk in top_chunks:
        answer_lines.append(f'1. {chunk.content}')
        references.append({'title': chunk.title, 'content': chunk.content})
    answer_lines.append('如果你要，我可以继续按“鉴权”“分钟数据”“数据范围”“交付”“保存周期”“目录调用”这些方向继续细问。')
    return {'ok': True, 'answer': '\n'.join(answer_lines), 'references': references}
