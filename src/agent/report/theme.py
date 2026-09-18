"""Tema (CSS/tokens claro-escuro), script de pagina e o documento HTML raiz.

Conteudo estatico (strings) movido de `report.py`, sem nenhuma logica alem da
composicao do documento em `_html_document`.
"""

from __future__ import annotations

import html

_DARK_TOKENS = """
  color-scheme: dark;
  --canvas: #0D1117;
  --surface: #171C24;
  --surface-2: #1E242E;
  --ink: #E6EDF3;
  --ink-soft: #C7D0DA;
  --muted: #98A2B3;
  --line: #2A313C;
  --line-soft: #232A34;
  --accent: #4FD1C0;
  --accent-soft: #14312E;
  --bad: #F59356;
  --bad-soft: #2E1D14;
  --bad-line: #4A2E1D;
  --warn: #E0B65C;
  --context-bg: #241E14;
  --context-line: #3D3320;
  --chip-bg: #212836;
  --shadow: 0 1px 2px rgba(0,0,0,.4);
"""

#: Tokens claros, repetidos dentro de `@media print`: a impressao ignora o tema
#: escolhido na tela -- fundo escuro em papel gasta tinta e apaga o texto.
_LIGHT_TOKENS = """
  color-scheme: light;
  --canvas: #FFFFFF;
  --surface: #FFFFFF;
  --surface-2: #F7F8FA;
  --ink: #111827;
  --ink-soft: #374151;
  --muted: #5B6675;
  --line: #D9DEE4;
  --line-soft: #EDF0F3;
  --accent: #0F6B62;
  --accent-soft: #E6F1EF;
  --bad: #A8451A;
  --bad-soft: #FBEDE4;
  --bad-line: #F0CBB2;
  --warn: #8A6318;
  --context-bg: #FCF8F0;
  --context-line: #EDE0C8;
  --chip-bg: #F1F3F5;
  --shadow: none;
"""

_HTML_STYLE = """
:root {
  color-scheme: light;
  --canvas: #F1F3F5;
  --surface: #FFFFFF;
  --surface-2: #F7F8FA;
  --ink: #111827;
  --ink-soft: #374151;
  --muted: #6B7280;
  --line: #E3E7EB;
  --line-soft: #EDF0F3;
  --accent: #0F6B62;
  --accent-soft: #E6F1EF;
  --bad: #C2521B;
  --bad-soft: #FBEDE4;
  --bad-line: #F0CBB2;
  --warn: #B5822A;
  --context-bg: #FCF8F0;
  --context-line: #EDE0C8;
  --chip-bg: #F1F3F5;
  --shadow: 0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06);
  --radius: 14px;
  --radius-sm: 10px;
  --topbar-h: 62px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {__DARK__} }
:root[data-theme="dark"] {__DARK__}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  color: var(--ink);
  background: var(--canvas);
  margin: 0;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); }

/* ---- Barra fixa -------------------------------------------------------- */
.topbar {
  position: sticky;
  top: 0;
  z-index: 50;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.topbar-inner {
  max-width: 1240px;
  margin: 0 auto;
  padding: .55rem 1.25rem;
  display: flex;
  align-items: center;
  gap: 1.25rem;
  min-height: var(--topbar-h);
}
.brand { display: flex; align-items: center; gap: .65rem; text-decoration: none; color: inherit; }
.brand-mark {
  width: 34px; height: 34px; border-radius: 9px;
  background: var(--accent); color: #fff;
  display: grid; place-items: center;
  font-size: .6rem; font-weight: 800; letter-spacing: .04em;
}
.brand-text { display: flex; flex-direction: column; line-height: 1.2; }
.brand-text b { font-size: .92rem; font-weight: 700; }
.brand-text small { font-size: .72rem; color: var(--muted); }
.navlinks { display: flex; gap: .15rem; flex: 1; flex-wrap: wrap; }
.nav-link {
  font-size: .82rem;
  font-weight: 600;
  color: var(--muted);
  text-decoration: none;
  padding: .4rem .7rem;
  border-radius: 999px;
  white-space: nowrap;
}
.nav-link:hover { color: var(--ink); background: var(--surface-2); }
.nav-link.is-active { color: var(--accent); background: var(--accent-soft); }
.topbar-actions { display: flex; align-items: center; gap: .5rem; }
.btn {
  display: inline-flex; align-items: center; gap: .4rem;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  border-radius: 999px;
  padding: .45rem 1rem;
  font-size: .8rem;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  white-space: nowrap;
}
.btn:hover { border-color: var(--accent); color: var(--accent); }
.icon-btn {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  border-radius: 999px;
  width: 34px; height: 34px;
  display: grid; place-items: center;
  cursor: pointer;
  font-size: .95rem;
  line-height: 1;
  font-family: inherit;
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); }

/* ---- Grade da pagina --------------------------------------------------- */
.report-shell { max-width: 1240px; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
section[id], details[id] { scroll-margin-top: calc(var(--topbar-h) + 14px); }
.eyebrow {
  text-transform: uppercase;
  letter-spacing: .09em;
  font-size: .7rem;
  font-weight: 700;
  color: var(--accent);
  margin: 0 0 .35rem;
}
.block {
  margin-top: 1.1rem;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.5rem 1.6rem;
}
.block-bare { background: none; border: 0; box-shadow: none; padding: 0; margin-top: 1.9rem; }
.block-headline {
  font-size: 1.24rem; font-weight: 700; margin: 0 0 .25rem; letter-spacing: -.01em;
}
.panel-head { margin-bottom: 1rem; }

/* ---- Capa -------------------------------------------------------------- */
.report-header {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.6rem 1.7rem;
  margin-top: 1.5rem;
}
.report-header h1 {
  font-size: 1.72rem; margin: 0 0 1rem; letter-spacing: -.02em; line-height: 1.22;
}
.delivery-label {
  margin: 0 0 .55rem;
  font-size: .74rem;
  font-weight: 700;
  letter-spacing: .04em;
  color: var(--ink);
}
.report-footer .delivery-label { margin: 0 0 .5rem; color: var(--ink); }
.hero-chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0; padding: 0; list-style: none; }
.chip {
  display: inline-flex; align-items: baseline; gap: .4rem;
  background: var(--chip-bg);
  border: 1px solid transparent;
  border-radius: 999px;
  padding: .32rem .8rem;
  font-size: .78rem;
  color: var(--ink-soft);
  white-space: nowrap;
}
.chip b { font-weight: 700; color: var(--ink); }
.chip-key {
  color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .06em;
}
.chip-accent { background: var(--accent-soft); color: var(--accent); }
.chip-accent .chip-key, .chip-accent b { color: var(--accent); }
.chip-warn { background: var(--bad-soft); border-color: var(--bad-line); color: var(--bad); }

/* ---- Resumo executivo e status ----------------------------------------- */
.exec-summary { background: var(--accent-soft); border-color: transparent; }
.exec-summary .block-headline { color: var(--ink); }
.exec-list { list-style: none; margin: 1rem 0 0; padding: 0; display: grid; gap: .55rem; }
.exec-list li { display: flex; gap: .6rem; align-items: baseline; font-size: .94rem; }
.tag {
  font-size: .62rem;
  font-weight: 800;
  letter-spacing: .06em;
  padding: .14rem .45rem;
  border-radius: 5px;
  flex: 0 0 auto;
}
.tag-dado { background: var(--accent); color: #fff; }
.tag-contexto { background: var(--warn); color: #fff; }

.status-strip {
  margin-top: 1.1rem;
  background: var(--surface);
  border: 1px solid var(--line);
  border-left: 5px solid var(--muted);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.1rem 1.35rem;
}
.status-normal { border-left-color: var(--accent); }
.status-warn { border-left-color: var(--warn); }
.status-bad { border-left-color: var(--bad); }
.status-head { display: flex; align-items: baseline; gap: .75rem; flex-wrap: wrap; }
.status-badge {
  font-size: .66rem;
  font-weight: 800;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--ink);
}
.status-summary { margin: 0; font-size: .9rem; color: var(--ink); }
.status-list { margin: .6rem 0 0; padding-left: 1.1rem; font-size: .86rem; }
.status-history { margin: .55rem 0 0; font-size: .78rem; color: var(--muted); }

/* ---- Indicadores ------------------------------------------------------- */
.context-label { margin: 1.4rem 0 .6rem; font-size: .78rem; color: var(--muted); }
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: .9rem; margin-top: .9rem; }
.kpi-grid-context { grid-template-columns: repeat(2, 1fr); margin-top: 0; }
.kpi-card {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.15rem 1.25rem;
  background: var(--surface);
}
.kpi-compact { background: var(--surface-2); box-shadow: none; padding: .95rem 1.1rem; }
.kpi-compact .kpi-value { font-size: 1.3rem; }
.kpi-compact .kpi-label { font-size: .7rem; }
.kpi-empty { background: var(--surface-2); box-shadow: none; }
.kpi-label {
  font-size: .71rem;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: .5rem;
}
.kpi-value { font-size: 2rem; font-weight: 800; letter-spacing: -.025em; line-height: 1.1; }
.kpi-unit {
  display: block;
  font-size: .72rem;
  font-weight: 600;
  color: var(--muted);
  letter-spacing: 0;
  margin-top: .15rem;
}
.kpi-muted { color: var(--muted); font-size: 1.1rem; font-weight: 700; }
.kpi-comparison { font-size: .8rem; color: var(--muted); margin-top: .4rem; min-height: 1.1em; }
.kpi-note {
  font-size: .73rem;
  color: var(--muted);
  margin-top: .6rem;
  border-top: 1px solid var(--line-soft);
  padding-top: .55rem;
}
.kpi-warning { font-size: .73rem; color: var(--bad); margin-top: .4rem; }
.kpi-trend { font-weight: 800; }
.trend-bad { color: var(--bad); }
.trend-good { color: var(--accent); }
.trend-neutral { color: var(--muted); }

/* ---- Comparador de indicadores ----------------------------------------- */
.cmp-wrap { margin-top: 1rem; overflow-x: auto; }
.cmp-wrap table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .86rem; }
.cmp-wrap thead th {
  text-align: left;
  font-size: .68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
  padding: 0 .7rem .5rem;
  white-space: nowrap;
  vertical-align: bottom;
}
.cmp-wrap thead th.cmp-num { text-align: right; }
.cmp-sortable button {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  color: inherit;
  letter-spacing: inherit;
  text-transform: inherit;
  cursor: pointer;
}
.cmp-sortable button::after { content: " ↕"; opacity: .35; }
.cmp-sortable[aria-sort="ascending"] button,
.cmp-sortable[aria-sort="descending"] button { color: var(--accent); }
.cmp-sortable[aria-sort="ascending"] button::after { content: " ↑"; opacity: 1; }
.cmp-sortable[aria-sort="descending"] button::after { content: " ↓"; opacity: 1; }

.cmp-row { cursor: pointer; }
.cmp-row td {
  padding: .7rem;
  border-bottom: 1px solid var(--line-soft);
  vertical-align: top;
}
.cmp-row:hover td { background: var(--surface-2); }
.cmp-row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.cmp-num { text-align: right; font-variant-numeric: tabular-nums; }
.cmp-value { font-weight: 700; font-size: 1rem; white-space: nowrap; }
.cmp-unit { display: block; font-size: .66rem; font-weight: 600; color: var(--muted); }
.cmp-null { color: var(--muted); font-weight: 400; }

.cmp-name { min-width: 190px; display: flex; align-items: flex-start; gap: .55rem; }
.cmp-dot {
  flex: 0 0 auto;
  width: 9px; height: 9px; border-radius: 50%;
  margin-top: .38rem; background: var(--muted);
}
.cmp-dot-0 { background: #0F6B62; }
.cmp-dot-1 { background: #C2521B; }
.cmp-dot-2 { background: #3B6FB6; }
.cmp-dot-3 { background: #7A5AA6; }
.cmp-dot-4 { background: #4B7B2E; }
.cmp-dot-5 { background: #B5822A; }
.cmp-dot-6 { background: #A33B5E; }
.cmp-name-text { display: inline-block; font-weight: 600; }
.cmp-name-text small {
  display: block;
  font-size: .66rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--muted);
}
.cmp-bar {
  display: block;
  height: 4px;
  margin-top: .35rem;
  border-radius: 999px;
  background: var(--line-soft);
  overflow: hidden;
}
.cmp-bar i { display: block; height: 100%; background: var(--accent); opacity: .75; }
.cmp-tag {
  display: inline-block;
  font-size: .66rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  padding: .18rem .5rem;
  border-radius: 999px;
}
.cmp-tag-ok { background: var(--accent-soft); color: var(--accent); }
.cmp-tag-off { background: var(--bad-soft); color: var(--bad); }

.cmp-detail td { padding: 0 .7rem 1.1rem; border-bottom: 1px solid var(--line-soft); }
.cmp-detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: .9rem 1.5rem;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  padding: 1rem 1.2rem;
}
.cmp-detail-wide { grid-column: 1 / -1; }
.cmp-detail-key {
  display: block;
  font-size: .66rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--muted);
  margin-bottom: .2rem;
}
.cmp-detail-grid p { margin: 0; font-size: .84rem; }
.cmp-detail-grid ul { margin: .2rem 0 0; padding-left: 1.1rem; font-size: .84rem; }
.cmp-detail-grid li { margin-bottom: .25rem; }
.cmp-warning { color: var(--bad); font-size: .82rem; margin-top: .5rem !important; }

/* ---- Graficos ---------------------------------------------------------- */
.chart-wrap {
  margin-top: 1rem;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  background: var(--surface);
  padding: .75rem .9rem .9rem;
}
.chart-interactive { width: 100%; min-height: 370px; }
.chart-print-only { display: none; width: 100%; border-radius: 6px; }
.chart-empty { color: var(--muted); font-size: .9rem; }

.insights-box {
  margin-top: 1rem;
  padding: 1rem 1.2rem;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}
.insights-title {
  font-weight: 700;
  font-size: .71rem;
  margin: 0 0 .5rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .06em;
}
.insights-box ul { margin: 0; padding-left: 1.1rem; font-size: .88rem; color: var(--ink); }
.insights-box li { margin-bottom: .3rem; }

/* ---- Contexto externo --------------------------------------------------- */
.news-intro { color: var(--muted); font-size: .87rem; margin: .1rem 0 0; }
.news-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: .9rem;
  margin-top: 1.2rem;
}
.news-card {
  background: var(--context-bg);
  border: 1px solid var(--context-line);
  border-radius: var(--radius-sm);
  padding: 1rem 1.1rem;
}
.news-title { font-weight: 700; font-size: .89rem; margin: 0 0 .5rem; }
.news-meta { font-size: .75rem; color: var(--muted); margin: 0 0 .6rem; }
.news-link { font-size: .78rem; font-weight: 700; color: var(--accent); text-decoration: none; }
.news-link-disabled { color: var(--muted); }
.news-empty { color: var(--muted); font-size: .9rem; }

/* ---- Interpretacao e transparencia -------------------------------------- */
.interpretation {
  background: var(--surface-2);
  border-left: 4px solid var(--accent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  padding: 1.2rem 1.4rem;
  margin-top: 1.2rem;
}
.interpretation p { margin: 0 0 .8rem; }
.interpretation p:last-child { margin-bottom: 0; }
.interpretation h4 { font-size: .92rem; color: var(--accent); margin: 1.1rem 0 .4rem; }
.interpretation h4:first-child { margin-top: 0; }
.interpretation ul { margin: 0 0 .8rem; padding-left: 1.2rem; }
.interpretation li { margin-bottom: .3rem; }
.interpretation-note {
  font-size: .78rem;
  color: var(--muted);
  margin-bottom: 1rem !important;
  font-style: italic;
}

.callout {
  border-radius: var(--radius-sm);
  padding: 1rem 1.2rem;
  margin-top: 1rem;
  background: var(--bad-soft);
  border: 1px solid var(--bad-line);
}
.callout-neutral { background: var(--surface-2); border-color: var(--line); }
.callout-bad { background: var(--bad-soft); border-color: var(--bad-line); }
.callout-title { font-weight: 700; font-size: .85rem; margin: 0 0 .35rem; }
.callout p { font-size: .87rem; margin: 0; }
.callout ul { margin: .3rem 0 0; padding-left: 1.1rem; font-size: .85rem; }

.methodology {
  margin-top: 1.5rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 1rem 1.2rem;
}
.methodology summary { cursor: pointer; font-weight: 700; font-size: .9rem; }
.methodology table { width: 100%; border-collapse: collapse; margin-top: .9rem; font-size: .85rem; }
.methodology table td {
  padding: .4rem 0; border-bottom: 1px solid var(--line-soft); vertical-align: top;
}
.meta-key { color: var(--muted); width: 40%; }

/* ---- Anexo tecnico ------------------------------------------------------ */
.technical-appendix { margin-top: 1.1rem; }
.technical-appendix summary {
  cursor: pointer; font-weight: 700; font-size: 1rem; color: var(--ink);
}
.technical-appendix-body { margin-top: 1.2rem; }
.technical-appendix-body h1 { display: none; }
.technical-appendix-body h2 {
  font-size: 1.04rem;
  color: var(--ink);
  border-bottom: 1px solid var(--line);
  padding-bottom: .35rem;
  margin-top: 2rem;
}
.technical-appendix-body h3 { font-size: .95rem; color: var(--accent); margin-top: 1.5rem; }
.technical-appendix-body table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  margin: .9rem 0;
  display: block;
  overflow-x: auto;
  font-size: .84rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}
.technical-appendix-body th, .technical-appendix-body td {
  border-bottom: 1px solid var(--line-soft);
  padding: .5rem .7rem;
  text-align: left;
  vertical-align: top;
}
.technical-appendix-body tr:last-child td { border-bottom: 0; }
.technical-appendix-body th {
  background: var(--surface-2);
  color: var(--muted);
  font-size: .72rem;
  text-transform: uppercase;
  letter-spacing: .05em;
  white-space: nowrap;
}
.technical-appendix-body tbody tr:hover td { background: var(--surface-2); }
.technical-appendix-body blockquote {
  border-left: 3px solid var(--accent);
  margin: .8rem 0;
  padding: .5rem .9rem;
  background: var(--surface-2);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.technical-appendix-body img {
  max-width: 100%; border: 1px solid var(--line); border-radius: 8px;
}
.technical-appendix-body code {
  background: var(--surface-2); padding: .1rem .3rem; border-radius: 4px; font-size: .88em;
}

.report-footer {
  margin-top: 1.5rem;
  padding: 1.2rem 1.6rem;
  border-top: 1px solid var(--line);
  font-size: .78rem;
  color: var(--muted);
}
.report-footer .disclaimer { margin-top: .4rem; font-style: italic; }

@media (max-width: 1080px) { .navlinks { display: none; } }
@media (max-width: 860px) {
  .kpi-grid, .kpi-grid-context { grid-template-columns: repeat(2, 1fr); }
  .block { padding: 1.2rem 1.1rem; }
  .report-header { padding: 1.3rem 1.2rem; }
}
@media print {
  :root, :root[data-theme="dark"], :root[data-theme="light"] {__LIGHT__}
  .no-print, .topbar { display: none !important; }
  body { background: #fff; }
  .report-shell { max-width: none; padding: 0; }
  .chart-interactive { display: none !important; }
  .chart-print-only { display: block !important; }
  /* So o que fica ilegivel partido ao meio e que nao pode quebrar. Aplicar
     isso a `.block` inteiro empurrava a secao seguinte para a proxima folha e
     deixava um terco de pagina em branco a cada corte. */
  .report-header, .status-strip, .chart-wrap, .insights-box { page-break-inside: avoid; }
  /* Titulo nunca fica sozinho no pe da folha nem partido ao meio. O
     `break-after` sozinho nao basta: quando o que vem depois e uma caixa
     indivisivel que nao cabe no resto da folha, o navegador ignora a regra --
     por isso os dois paineis de grafico sao indivisiveis por inteiro. */
  .panel-head, .block-headline, .eyebrow { page-break-after: avoid; break-after: avoid; }
  .block-headline { break-inside: avoid; }
  #series, #series-mensal { page-break-inside: avoid; }
  .callout, .kpi-card, .news-card, .cmp-row, .cmp-detail { break-inside: avoid; }
  .cmp-sortable button::after { content: "" !important; }
  .cmp-wrap { overflow-x: visible; }
  .cmp-hint { display: none; }
}
""".replace("__DARK__", _DARK_TOKENS).replace("__LIGHT__", _LIGHT_TOKENS)

#: Roda no `<head>`, antes do primeiro pixel: aplica o tema salvo na visita
#: anterior. Depois da pagina pintada, a troca apareceria como um flash branco.
_THEME_BOOTSTRAP = """
<script>
(function () {
  try {
    var saved = localStorage.getItem("srag-theme");
    if (saved === "dark" || saved === "light") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (error) {
    /* armazenamento bloqueado: segue a preferencia do sistema */
  }
})();
</script>
"""

#: Script da pagina: tema, repintura dos graficos, navegacao e o fallback de
#: rede. Vai no fim do `<body>` -- quando ele roda, os componentes de grafico
#: ja se registraram em `window.__sragCharts`.
_PAGE_SCRIPT = """
<script>
(function () {
  var root = document.documentElement;
  var query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function currentTheme() {
    return root.getAttribute("data-theme") || (query && query.matches ? "dark" : "light");
  }

  // Um SVG do Plotly nao herda cor de CSS: cada grafico registra o ajuste das
  // duas paletas e a troca de tema reaplica o do tema corrente.
  window.__sragPaintCharts = function () {
    if (typeof Plotly === "undefined") { return; }
    var registry = window.__sragCharts || {};
    var theme = currentTheme();
    Object.keys(registry).forEach(function (id) {
      var element = document.getElementById(id);
      var spec = registry[id][theme];
      if (!element || !element.data || !spec) { return; }
      Plotly.relayout(element, spec.layout);
      (spec.traces || []).forEach(function (patch) {
        // Cada valor vai embrulhado numa lista de um item: e assim que o
        // restyle diz "este valor e do trace tal". Sem o embrulho, uma cor que
        // ja e lista -- as barras do grafico mensal, uma cor por mes -- seria
        // lida como uma cor por TRACE, e o mes de pico perdia o destaque.
        var update = {};
        Object.keys(patch[1]).forEach(function (key) { update[key] = [patch[1][key]]; });
        Plotly.restyle(element, update, [patch[0]]);
      });
    });
  };

  function syncToggle() {
    var button = document.getElementById("theme-toggle");
    if (!button) { return; }
    var dark = currentTheme() === "dark";
    button.innerHTML = dark ? "&#x2600;" : "&#x263E;";
    button.setAttribute("aria-label", dark ? "Usar tema claro" : "Usar tema escuro");
  }

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    try { localStorage.setItem("srag-theme", theme); } catch (error) { /* sem storage */ }
    syncToggle();
    window.__sragPaintCharts();
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) { return; }
    if (target.closest("#theme-toggle")) {
      applyTheme(currentTheme() === "dark" ? "light" : "dark");
      return;
    }
    if (target.closest("#print-button")) {
      window.print();
      return;
    }
    // O anexo tecnico e recolhido: o link de rastreabilidade tem de abri-lo,
    // senao a ancora leva para um titulo fechado.
    if (target.closest('a[href="#anexo-tecnico"]')) {
      var appendix = document.getElementById("anexo-tecnico");
      if (appendix) { appendix.open = true; }
    }
  });

  if (query && query.addEventListener) {
    query.addEventListener("change", function () {
      if (!root.getAttribute("data-theme")) { syncToggle(); window.__sragPaintCharts(); }
    });
  }

  // Tabela comparativa: ordenacao por coluna e detalhe por linha.
  //
  // Cada linha de dado vem seguida da sua linha de detalhe; ordenar move o par
  // junto, senao o detalhe de um indicador apareceria sob outro. Celula sem
  // valor (`data-sort` vazio) vai sempre para o fim, nas duas direcoes: um
  // indicador indisponivel nao "vale zero", ele nao tem valor.
  (function () {
    var table = document.querySelector(".cmp-wrap table");
    if (!table) { return; }
    var body = table.tBodies[0];

    function pares() {
      return Array.prototype.map.call(body.querySelectorAll(".cmp-row"), function (linha) {
        return [linha, body.querySelector('[data-detail-for="' + linha.dataset.key + '"]')];
      });
    }

    function ordenar(indice, tipo, crescente) {
      var linhas = pares();
      linhas.sort(function (a, b) {
        var ta = a[0].cells[indice].getAttribute("data-sort") || "";
        var tb = b[0].cells[indice].getAttribute("data-sort") || "";
        if (ta === "" || tb === "") { return ta === tb ? 0 : (ta === "" ? 1 : -1); }
        if (tipo === "number") {
          return (crescente ? 1 : -1) * (parseFloat(ta) - parseFloat(tb));
        }
        return (crescente ? 1 : -1) * ta.localeCompare(tb, "pt-BR");
      });
      linhas.forEach(function (par) {
        body.appendChild(par[0]);
        if (par[1]) { body.appendChild(par[1]); }
      });
    }

    Array.prototype.forEach.call(table.querySelectorAll("th.cmp-sortable"), function (th, indice) {
      th.addEventListener("click", function () {
        var crescente = th.getAttribute("aria-sort") !== "ascending";
        Array.prototype.forEach.call(table.querySelectorAll("th"), function (outro) {
          outro.setAttribute("aria-sort", "none");
        });
        th.setAttribute("aria-sort", crescente ? "ascending" : "descending");
        ordenar(indice, th.getAttribute("data-type"), crescente);
      });
    });

    function alternar(linha) {
      var detalhe = body.querySelector('[data-detail-for="' + linha.dataset.key + '"]');
      if (!detalhe) { return; }
      var aberto = !detalhe.hidden;
      detalhe.hidden = aberto;
      linha.setAttribute("aria-expanded", String(!aberto));
    }

    body.addEventListener("click", function (evento) {
      var linha = evento.target.closest ? evento.target.closest(".cmp-row") : null;
      if (linha) { alternar(linha); }
    });
    body.addEventListener("keydown", function (evento) {
      if (evento.key !== "Enter" && evento.key !== " ") { return; }
      var linha = evento.target.closest ? evento.target.closest(".cmp-row") : null;
      if (linha) { evento.preventDefault(); alternar(linha); }
    });
  })();

  var links = Array.prototype.slice.call(document.querySelectorAll(".nav-link"));
  var targets = links.map(function (link) {
    return document.querySelector(link.getAttribute("href"));
  });
  if (window.IntersectionObserver && targets.length) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        var index = targets.indexOf(entry.target);
        links.forEach(function (link, position) {
          link.classList.toggle("is-active", position === index);
        });
      });
    }, { rootMargin: "-80px 0px -60% 0px", threshold: 0 });
    targets.forEach(function (target) { if (target) { observer.observe(target); } });
  }

  syncToggle();

  // Troca o grafico interativo pelo PNG estatico do mesmo grafico, por par
  // `.chart-wrap` -- nunca em bloco, para que a falha de um grafico nao
  // derrube o outro.
  function useStaticFallback(wrap) {
    var fallback = wrap.querySelector(".chart-print-only");
    if (!fallback) { return; }
    var interactive = wrap.querySelector(".chart-interactive");
    if (interactive) { interactive.style.display = "none"; }
    fallback.style.display = "block";
  }

  // Aplica o fallback a todos os graficos, inclusive aos que ainda nao estao
  // no DOM. Isso nao e paranoia: `htmlpreview.github.io` -- o visualizador do
  // link publicado no README -- AVALIA OS <script> ANTES de injetar o corpo do
  // documento. Quando este codigo roda ali, `.chart-wrap` ainda nao existe, e
  // uma troca de passada unica nao encontra nada para trocar.
  //
  // A espera e por OBSERVACAO, nao por prazo: uma versao anterior tentava em
  // laco por 5 segundos e ainda assim perdia a janela quando o visualizador
  // demorava mais do que isso para injetar o corpo -- exatamente o que foi
  // medido. O observer reage no instante em que os graficos aparecem, sem
  // depender de quanto tempo isso leve. A troca e idempotente.
  function applyStaticFallback() {
    function aplicar() {
      var wraps = document.querySelectorAll(".chart-wrap");
      wraps.forEach(useStaticFallback);
      // Alvo lido a cada passada: o registro pode ser populado depois desta
      // funcao comecar, e o corpo pode ser injetado em partes -- parar no
      // primeiro grafico que aparece deixaria o segundo em branco.
      var alvo = Object.keys(window.__sragCharts || {}).length;
      return alvo > 0 && wraps.length >= alvo;
    }

    if (aplicar()) { return; }
    if (!window.MutationObserver) { return; }

    var observer = new MutationObserver(function () {
      if (aplicar()) { observer.disconnect(); }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    // Rede de seguranca: nenhum observer fica vivo indefinidamente.
    window.setTimeout(function () { observer.disconnect(); }, 30000);
  }

  // O fallback NAO pode depender do evento `load`, e este era o defeito: num
  // visualizador que injeta o documento depois que a pagina ja carregou, o
  // `load` ja ocorreu e o listener nunca disparava. Pior, ali o <script src>
  // do Plotly entra no DOM por innerHTML e por isso NUNCA executa -- entao o
  // grafico interativo nao era pintado e o PNG seguia escondido: dois
  // retangulos vazios no lugar dos dois graficos.
  //
  // Este bloco roda no fim do <body>, quando o <script src> do Plotly (que e
  // sincrono, no <head>) ja terminou -- com sucesso ou nao. Nesse ponto
  // `Plotly === undefined` e conclusivo, e a troca pode ser decidida.
  function settleCharts() {
    if (typeof Plotly === "undefined") {
      applyStaticFallback();
      return;
    }
    window.__sragPaintCharts();
    // Segunda passada: cobre o caso em que o Plotly existe mas `newPlot`
    // falhou (dado invalido, erro do proprio CDN). O atraso da tempo ao
    // `newPlot`, que e assincrono, de inserir o SVG antes da verificacao.
    window.setTimeout(function () {
      document.querySelectorAll(".chart-wrap").forEach(function (wrap) {
        var interactive = wrap.querySelector(".chart-interactive");
        if (interactive && interactive.children.length === 0) { useStaticFallback(wrap); }
      });
    }, 1200);
  }

  settleCharts();
  // O `load` continua repintando o tema: numa carga normal ele chega depois
  // das fontes, e o Plotly so acerta as cores com o layout ja estabilizado.
  window.addEventListener("load", function () {
    if (typeof Plotly !== "undefined") { window.__sragPaintCharts(); }
  });
})();
</script>
"""


def _html_document(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>{_HTML_STYLE}</style>
{_THEME_BOOTSTRAP}
</head>
<body>
{body}
{_PAGE_SCRIPT}
</body>
</html>
"""
