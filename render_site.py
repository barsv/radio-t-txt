"""Render completed episodes as a domain-independent static site."""
import html
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORY_URL = os.environ.get('RADIO_T_REPOSITORY_URL', 'https://github.com/barsv/radio-t-txt').strip()


def e(value):
    return html.escape(str(value), quote=True)


def timestamp(seconds):
    n = int(seconds)
    return f"{n//3600:02}:{n//60%60:02}:{n%60:02}"


def shell(title, content, assets, extra=""):
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="description" content="Радио-Т: расшифровки выпусков, краткие резюме тем и навигация по времени.">
<title>{e(title)} · Радио-Т / текст</title><link rel="stylesheet" href="{assets}style.css">
<script src="{assets}player.js" defer></script></head><body>{content}{extra}</body></html>'''


def masthead(home):
    return f'<header class="masthead"><a class="brand" href="{home}"><span class="brand-mark" aria-hidden="true">р/т</span> Радио-Т <span class="brand-light">/ текст</span></a><span class="edition">Слушать. Читать. Находить.</span></header>'


def footer(source):
    repository_link = f'<br><a href="{e(REPOSITORY_URL)}">Исходный код на GitHub ↗</a>' if REPOSITORY_URL else ''
    return f'<footer class="footer">Неофициальный текстовый архив. Расшифровка и резюме созданы автоматически.<br><a href="{e(source)}">Оригинал — подкаст Радио-Т</a>. Формулировки могут отличаться от записи.{repository_link}</footer>'


def episode_page(data, summaries):
    n = data['number']
    summary_by_id = {c['id']: c for c in summaries['chapters']}
    navigation = ''.join(f'<a href="#{e(c["id"])}"><time>{timestamp(c["start"])}</time><span>{e(c["title"])}</span></a>' for c in data['chapters'])
    sections = []
    plain = []
    captions = []
    for chapter in data['chapters']:
        summary = summary_by_id[chapter['id']]
        bullets = ''.join(f'<li>{e(b)}</li>' for b in summary['bullets'])
        blocks = []
        plain.append(f'\n{timestamp(chapter["start"])} — {chapter["title"]}\n')
        for block in chapter['blocks']:
            anchor = f't-{round(block["start"]*1000)}'
            blocks.append(f'<p class="transcript-block" id="{anchor}" data-start="{block["start"]}" data-end="{block["end"]}"><a class="time-link" href="#{anchor}" data-seek="{block["start"]}" aria-label="Слушать с {timestamp(block["start"])}">{timestamp(block["start"])}</a><span>{e(block["text"])}</span></p>')
            plain.append(f'[{timestamp(block["start"])}] {block["text"]}\n')
            def vtt_time(seconds):
                ms = round(seconds*1000)
                return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'
            captions.append(f'{vtt_time(block["start"])} --> {vtt_time(block["end"])}\n{block["text"]}\n')
        sections.append(f'''<section class="chapter" id="{e(chapter['id'])}">
<div class="chapter-heading"><span class="eyebrow">{timestamp(chapter['start'])}</span><button class="listen-small" data-seek="{chapter['start']}" type="button">▶ Слушать тему</button></div>
<h2>{e(chapter['title'])}</h2><div class="summary"><span class="summary-label">Коротко</span><p>{e(summary['summary'])}</p><ul>{bullets}</ul></div>
<details class="transcript" open><summary>Расшифровка темы</summary>{''.join(blocks)}</details></section>''')
    date = '.'.join(reversed(data['date'].split('-')))
    body = masthead('../') + f'''<div class="episode-layout"><aside class="contents"><a class="back-link" href="../">← Все выпуски</a><h2>В этом выпуске</h2><nav aria-label="Темы выпуска">{navigation}</nav><a class="download" href="transcript.txt" download>↓ Скачать текст</a><a class="download" href="transcript.vtt" download>↓ Скачать таймкоды VTT</a></aside>
<main id="main"><div class="episode-intro"><p class="eyebrow">Выпуск {n} · {date} · {timestamp(data['duration'])}</p><h1>Радио-Т <span>{n}</span></h1><p class="lede">{e(summaries['overview'])}</p><a class="source-link" href="{e(data['source_url'])}">Открыть оригинальный выпуск ↗</a></div>
<p class="reading-hint">Оглавление ведёт к темам. Время слева от текста запускает запись с нужного места.</p>{''.join(sections)}{footer(data['source_url'])}</main></div>'''
    player = f'''<div class="player-dock" role="region" aria-label="Аудиоплеер"><div class="player-title"><span class="live-dot"></span> Радио-Т {n}<span id="current-topic">Аудиозапись выпуска</span></div><audio id="podcast" controls preload="none" src="{e(data['audio_url'])}"></audio><label class="speed-label">Скорость<select id="speed" aria-label="Скорость воспроизведения"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select></label><label class="follow-label"><input type="checkbox" id="follow"> Следить за текстом</label><span id="player-message" role="status"></span></div>'''
    return shell(data['title'], body, '../assets/', player), '\n'.join(plain), 'WEBVTT\n\n'+'\n'.join(captions)


def build():
    dist = ROOT / 'dist'
    staging = ROOT / 'dist.next'
    if staging.exists():
        shutil.rmtree(staging)
    (staging/'assets').mkdir(parents=True)
    for name in ['style.css', 'player.js']:
        shutil.copyfile(ROOT/'web'/name, staging/'assets'/name)
    cards = []
    for folder in sorted((ROOT/'state').iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0, reverse=True):
        if not (folder/'complete.json').exists():
            continue
        data = json.loads((folder/'transcript.json').read_text())
        summaries = json.loads((folder/'summary.json').read_text())
        page, text, captions = episode_page(data, summaries)
        destination = staging/str(data['number'])
        destination.mkdir()
        (destination/'index.html').write_text(page, encoding='utf-8')
        (destination/'transcript.txt').write_text(text+'\n', encoding='utf-8')
        (destination/'transcript.vtt').write_text(captions, encoding='utf-8')
        (destination/'transcript.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        topics = ''.join(f'<span>{e(c["title"])}</span>' for c in data['chapters'][1:4])
        cards.append(f'<article class="episode-card"><p class="eyebrow">{e(data["date"])} · {timestamp(data["duration"])}</p><h2><a href="{data["number"]}/">Радио-Т {data["number"]} <span aria-hidden="true">↗</span></a></h2><p>{e(summaries["overview"])}</p><div class="topic-tags">{topics}</div><a class="read-link" href="{data["number"]}/">Читать выпуск →</a></article>')
    archive = masthead('./') + '<main class="archive"><p class="eyebrow">Технологии в разговоре</p><h1>Радио-Т.<br><span>Теперь и текстом.</span></h1><p class="archive-lede">Кратко о главном, полный разговор и таймкоды,<br>чтобы вернуться ровно к тому, что интересно.</p><div class="archive-section-title"><h2>Выпуски</h2><span>Новые сверху</span></div>'+''.join(cards)+footer('https://radio-t.com/')+'</main>'
    (staging/'index.html').write_text(shell('Выпуски', archive, 'assets/'), encoding='utf-8')
    # Build in a sibling directory first; never expose a half-rendered episode.
    previous = ROOT/'dist.previous'
    if previous.exists():
        shutil.rmtree(previous)
    if dist.exists():
        dist.rename(previous)
    staging.rename(dist)
    print(f'Static site built: {dist}; episodes: {len(cards)}', flush=True)


if __name__ == '__main__':
    build()
