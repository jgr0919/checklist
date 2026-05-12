"""
Generates a static HTML user manual from checklist data and publishes to S3.

Entry point: generate_and_publish(...)
Returns: {'url': str, 'files_uploaded': int, 'generated_at': str}
"""

from datetime import datetime
from html import escape
from botocore.exceptions import ClientError

MANUAL_PREFIX = 'manual'

# ── CSS ──────────────────────────────────────────────────────────────────────

SHARED_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f5;
  color: #1a1a2e;
  line-height: 1.6;
}

.site-header {
  background: #1a2b4a;
  color: white;
  padding: 1.25rem 2rem;
}
.site-header h1 { font-size: 1.4rem; font-weight: 700; }
.site-header .subtitle { font-size: .875rem; opacity: .75; margin-top: .2rem; }

.breadcrumb {
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  padding: .6rem 2rem;
  font-size: .875rem;
  color: #6b7280;
}
.breadcrumb a { color: #3b82f6; text-decoration: none; }
.breadcrumb a:hover { text-decoration: underline; }
.breadcrumb span { margin: 0 .4rem; }

.container {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}

h2 { font-size: 1.2rem; font-weight: 700; color: #1a2b4a; margin-bottom: 1rem; }

.toc-section {
  background: white;
  border-radius: 10px;
  box-shadow: 0 1px 6px rgba(0,0,0,.07);
  margin-bottom: 1.5rem;
  overflow: hidden;
}
.toc-section-header {
  display: flex;
  align-items: center;
  gap: .75rem;
  padding: 1rem 1.5rem;
  border-bottom: 1px solid #f3f4f6;
}
.team-color-dot {
  width: 14px; height: 14px;
  border-radius: 50%;
  flex-shrink: 0;
}
.toc-section-header h2 { font-size: 1.05rem; margin-bottom: 0; }
.toc-items { padding: .75rem 1.5rem 1rem; }
.toc-role-group { margin-bottom: 1rem; }
.toc-role-label {
  font-size: .7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .07em;
  color: #9ca3af;
  margin-bottom: .4rem;
}
.toc-link {
  display: block;
  padding: .5rem .75rem;
  border-radius: 6px;
  text-decoration: none;
  color: #1a2b4a;
  font-weight: 500;
  font-size: .95rem;
  margin-bottom: .2rem;
}
.toc-link:hover { background: #f3f4f6; }
.checklist-count {
  font-size: .8rem;
  color: #9ca3af;
  background: #f3f4f6;
  border-radius: 999px;
  padding: .1rem .5rem;
  margin-left: .5rem;
}

.checklist-header {
  background: white;
  border-radius: 10px;
  box-shadow: 0 1px 6px rgba(0,0,0,.07);
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}
.checklist-meta { display: flex; gap: .75rem; flex-wrap: wrap; margin-top: .75rem; }
.meta-chip {
  display: inline-flex;
  align-items: center;
  gap: .35rem;
  background: #f3f4f6;
  border-radius: 999px;
  padding: .25rem .75rem;
  font-size: .8rem;
  font-weight: 500;
  color: #374151;
}
.meta-chip.team-chip { color: white; }

.diagram-section {
  background: white;
  border-radius: 10px;
  box-shadow: 0 1px 6px rgba(0,0,0,.07);
  padding: 1.5rem;
  margin-bottom: 1.5rem;
  overflow-x: auto;
}

.steps-section { display: flex; flex-direction: column; gap: 1rem; margin-bottom: 2rem; }
.step-card {
  background: white;
  border-radius: 10px;
  box-shadow: 0 1px 6px rgba(0,0,0,.07);
  overflow: hidden;
  display: flex;
}
.step-number {
  background: #1a2b4a;
  color: white;
  font-size: 1.1rem;
  font-weight: 700;
  min-width: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.step-number.required { background: #dc2626; }
.step-body { padding: 1rem 1.25rem; flex: 1; }
.step-title { font-size: 1rem; font-weight: 700; margin-bottom: .3rem; }
.step-desc { font-size: .9rem; color: #4b5563; margin-top: .4rem; }
.required-badge {
  display: inline-block;
  background: #fee2e2;
  color: #dc2626;
  font-size: .72rem;
  font-weight: 600;
  border-radius: 4px;
  padding: .15rem .5rem;
  margin-bottom: .35rem;
}
.visual-aid {
  display: block;
  max-width: 420px;
  max-height: 300px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  margin-top: .85rem;
}

.org-chart-section {
  background: white;
  border-radius: 10px;
  box-shadow: 0 1px 6px rgba(0,0,0,.07);
  padding: 1.5rem;
  margin-bottom: 2rem;
  overflow-x: auto;
}

.site-footer {
  text-align: center;
  padding: 2rem;
  color: #9ca3af;
  font-size: .8rem;
  border-top: 1px solid #e5e7eb;
  background: white;
}

@media print {
  .site-header, .step-number {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
}
"""

# ── SVG Generators ────────────────────────────────────────────────────────────

_FONT = 'font-family="system-ui,-apple-system,sans-serif"'


def generate_flowchart_svg(checklist_name, items):
    """Vertical step flowchart: START → steps → SIGN OFF."""
    W = 440
    BOX_W = 340
    BOX_H = 66
    CX = W // 2
    ARROW_H = 36
    PAD = 14
    PILL_H = 42
    PILL_RX = 21

    def _arrow(ay1, ay2):
        return (f'<line x1="{CX}" y1="{ay1}" x2="{CX}" y2="{ay2 - 7}" '
                f'stroke="#94a3b8" stroke-width="2" marker-end="url(#arr)"/>')

    parts = []
    y = 44

    # START pill
    px = CX - 60
    parts.append(f'<rect x="{px}" y="{y}" width="120" height="{PILL_H}" rx="{PILL_RX}" fill="#1a2b4a"/>')
    parts.append(f'<text x="{CX}" y="{y + 27}" text-anchor="middle" font-size="14" font-weight="bold" fill="white" {_FONT}>START</text>')
    y += PILL_H

    for i, item in enumerate(items):
        parts.append(_arrow(y, y + ARROW_H))
        y += ARROW_H

        bx = CX - BOX_W // 2
        is_req = bool(getattr(item, 'is_required', True))
        fill = '#fff5f5' if is_req else '#f0f4ff'
        stroke = '#dc2626' if is_req else '#3b82f6'

        parts.append(f'<rect x="{bx}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')

        label = f'Step {i + 1}' + ('  ★ Required' if is_req else '')
        parts.append(f'<text x="{bx + PAD}" y="{y + 20}" font-size="11" fill="#6b7280" {_FONT}>{escape(label)}</text>')

        title = item.title if len(item.title) <= 46 else item.title[:46] + '…'
        parts.append(f'<text x="{bx + PAD}" y="{y + 46}" font-size="13" font-weight="bold" fill="#1a2b4a" {_FONT}>{escape(title)}</text>')

        if getattr(item, 'visual_aid_photo', None):
            parts.append(f'<text x="{bx + BOX_W - PAD}" y="{y + 46}" text-anchor="end" font-size="11" fill="#3b82f6" {_FONT}>&#128247; photo</text>')

        y += BOX_H + 4

    # Arrow + SIGN OFF pill
    parts.append(_arrow(y, y + ARROW_H))
    y += ARROW_H
    sx = CX - 70
    parts.append(f'<rect x="{sx}" y="{y}" width="140" height="{PILL_H}" rx="{PILL_RX}" fill="#16a34a"/>')
    parts.append(f'<text x="{CX}" y="{y + 27}" text-anchor="middle" font-size="14" font-weight="bold" fill="white" {_FONT}>SIGN OFF</text>')
    y += PILL_H + 24

    title_line = (f'<text x="{CX}" y="22" text-anchor="middle" font-size="12" fill="#6b7280" {_FONT}>'
                  f'{escape(checklist_name[:70])}</text>')

    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{y}" viewBox="0 0 {W} {y}">'
        f'<defs>'
        f'<marker id="arr" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">'
        f'<polygon points="0 0, 8 3, 0 6" fill="#94a3b8"/>'
        f'</marker>'
        f'</defs>'
    )
    return header + title_line + ''.join(parts) + '</svg>'


def generate_org_chart_svg(teams_data, unassigned):
    """Hierarchical org chart: Manual → Teams (with checklist counts)."""
    all_teams = []
    for td in teams_data:
        team = td['team']
        roles = list(td['roles'].keys())
        all_teams.append({
            'name': team.name,
            'color': team.color or '#4a6fa5',
            'count': len(td['checklists']),
            'roles': roles,
        })
    if unassigned:
        all_teams.append({'name': 'Unassigned', 'color': '#94a3b8', 'count': len(unassigned), 'roles': []})

    n = len(all_teams)
    if n == 0:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="80">'
                f'<text x="150" y="45" text-anchor="middle" font-size="13" fill="#9ca3af" {_FONT}>'
                f'No teams defined</text></svg>')

    TEAM_W = 180
    TEAM_H = 80
    GAP = 30
    total_w = max(520, n * (TEAM_W + GAP) + GAP)
    CX = total_w // 2

    parts = []
    y = 20

    ROOT_W, ROOT_H = 280, 50
    rx = CX - ROOT_W // 2
    parts.append(f'<rect x="{rx}" y="{y}" width="{ROOT_W}" height="{ROOT_H}" rx="10" fill="#1a2b4a"/>')
    parts.append(f'<text x="{CX}" y="{y + 32}" text-anchor="middle" font-size="15" font-weight="bold" fill="white" {_FONT}>Work Instructions Manual</text>')
    y += ROOT_H

    bar_y = y + 50
    parts.append(f'<line x1="{CX}" y1="{y}" x2="{CX}" y2="{bar_y}" stroke="#cbd5e1" stroke-width="2"/>')

    all_x = [GAP + i * (TEAM_W + GAP) + TEAM_W // 2 for i in range(n)]

    if n > 1:
        parts.append(f'<line x1="{all_x[0]}" y1="{bar_y}" x2="{all_x[-1]}" y2="{bar_y}" stroke="#cbd5e1" stroke-width="2"/>')

    team_top = bar_y + 20
    for i, td in enumerate(all_teams):
        tx = all_x[i] - TEAM_W // 2
        parts.append(f'<line x1="{all_x[i]}" y1="{bar_y}" x2="{all_x[i]}" y2="{team_top}" stroke="#cbd5e1" stroke-width="2"/>')
        parts.append(f'<rect x="{tx}" y="{team_top}" width="{TEAM_W}" height="{TEAM_H}" rx="8" fill="{td["color"]}"/>')

        name = td['name'][:20] + '…' if len(td['name']) > 20 else td['name']
        parts.append(f'<text x="{all_x[i]}" y="{team_top + 28}" text-anchor="middle" font-size="13" font-weight="bold" fill="white" {_FONT}>{escape(name)}</text>')

        cl_label = f'{td["count"]} instruction{"s" if td["count"] != 1 else ""}'
        parts.append(f'<text x="{all_x[i]}" y="{team_top + 48}" text-anchor="middle" font-size="11" fill="rgba(255,255,255,.8)" {_FONT}>{cl_label}</text>')

        if td['roles']:
            roles_str = ', '.join(r for r in td['roles'][:3] if r != 'General')
            if len(td['roles']) > 3:
                roles_str += '…'
            if roles_str:
                parts.append(f'<text x="{all_x[i]}" y="{team_top + 66}" text-anchor="middle" font-size="10" fill="rgba(255,255,255,.65)" {_FONT}>{escape(roles_str)}</text>')

    total_h = team_top + TEAM_H + 30
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
            f'viewBox="0 0 {total_w} {total_h}">'
            + ''.join(parts) + '</svg>')


# ── HTML Generators ───────────────────────────────────────────────────────────

def _header(title, subtitle):
    return (f'<header class="site-header">'
            f'<h1>{escape(title)}</h1>'
            f'<div class="subtitle">{escape(subtitle)}</div>'
            f'</header>')


def _footer(generated_at):
    return (f'<footer class="site-footer">'
            f'Generated {escape(generated_at)} &nbsp;|&nbsp; Work Instructions Manual'
            f'</footer>')


def generate_index_page(teams_data, unassigned, org_svg, generated_at):
    toc_html = ''
    for td in teams_data:
        team = td['team']
        color = team.color or '#4a6fa5'
        items_html = ''
        if td['roles']:
            for role, checklists in sorted(td['roles'].items()):
                items_html += (f'<div class="toc-role-group">'
                               f'<div class="toc-role-label">{escape(role)}</div>')
                for c in checklists:
                    n = len(c.items)
                    items_html += (f'<a href="checklist-{c.id}.html" class="toc-link">'
                                   f'{escape(c.name)}'
                                   f'<span class="checklist-count">{n} step{"s" if n != 1 else ""}</span>'
                                   f'</a>')
                items_html += '</div>'
        else:
            for c in td['checklists']:
                n = len(c.items)
                items_html += (f'<a href="checklist-{c.id}.html" class="toc-link">'
                               f'{escape(c.name)}'
                               f'<span class="checklist-count">{n} step{"s" if n != 1 else ""}</span>'
                               f'</a>')
        if items_html:
            toc_html += (f'<div class="toc-section">'
                         f'<div class="toc-section-header">'
                         f'<div class="team-color-dot" style="background:{escape(color)}"></div>'
                         f'<h2>{escape(team.name)}</h2>'
                         f'</div>'
                         f'<div class="toc-items">{items_html}</div>'
                         f'</div>')

    if unassigned:
        items_html = ''.join(
            f'<a href="checklist-{c.id}.html" class="toc-link">{escape(c.name)}'
            f'<span class="checklist-count">{len(c.items)} step{"s" if len(c.items) != 1 else ""}</span></a>'
            for c in unassigned
        )
        toc_html += (f'<div class="toc-section">'
                     f'<div class="toc-section-header">'
                     f'<div class="team-color-dot" style="background:#94a3b8"></div>'
                     f'<h2>Unassigned</h2>'
                     f'</div>'
                     f'<div class="toc-items">{items_html}</div>'
                     f'</div>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Work Instructions Manual</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
{_header('Work Instructions Manual', 'Complete reference for all active work instructions')}
<div class="container">
  <div class="org-chart-section">
    <h2>Organization Overview</h2>
    <div style="overflow-x:auto">{org_svg}</div>
  </div>
  {toc_html}
</div>
{_footer(generated_at)}
</body>
</html>"""


def generate_checklist_page(checklist, items, image_url_fn, flowchart_svg, generated_at):
    team = checklist.team
    team_color = team.color if team else '#94a3b8'
    team_name = team.name if team else None

    meta_chips = ''
    if team_name:
        meta_chips += f'<span class="meta-chip team-chip" style="background:{escape(team_color)}">{escape(team_name)}</span>'
    if checklist.assigned_role:
        meta_chips += f'<span class="meta-chip">{escape(checklist.assigned_role)}</span>'
    n = len(items)
    meta_chips += f'<span class="meta-chip">{n} step{"s" if n != 1 else ""}</span>'

    steps_html = ''
    for i, item in enumerate(items):
        req_badge = '<span class="required-badge">Required</span>' if item.is_required else ''
        desc_html = f'<p class="step-desc">{escape(item.description)}</p>' if item.description else ''
        img_html = ''
        if item.visual_aid_photo:
            url = image_url_fn(item.visual_aid_photo)
            if url:
                img_html = (f'<img src="{escape(url)}" '
                            f'alt="Visual aid for step {i + 1}" '
                            f'class="visual-aid">')

        num_class = 'step-number required' if item.is_required else 'step-number'
        steps_html += (f'<div class="step-card">'
                       f'<div class="{num_class}">{i + 1}</div>'
                       f'<div class="step-body">'
                       f'{req_badge}'
                       f'<div class="step-title">{escape(item.title)}</div>'
                       f'{desc_html}'
                       f'{img_html}'
                       f'</div>'
                       f'</div>')

    desc_block = (f'<p style="color:#4b5563;margin-top:.5rem">{escape(checklist.description)}</p>'
                  if checklist.description else '')

    breadcrumb_team = escape(team_name) if team_name else 'Unassigned'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(checklist.name)} – Work Instructions</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
{_header('Work Instructions Manual', checklist.name)}
<div class="breadcrumb">
  <a href="index.html">Manual Home</a>
  <span>&#8250;</span>
  {breadcrumb_team}
  <span>&#8250;</span>
  {escape(checklist.name)}
</div>
<div class="container">
  <div class="checklist-header">
    <h2>{escape(checklist.name)}</h2>
    {desc_block}
    <div class="checklist-meta">{meta_chips}</div>
  </div>
  <div class="diagram-section">
    <h2>Process Flow</h2>
    <div style="overflow-x:auto">{flowchart_svg}</div>
  </div>
  <h2>Steps</h2>
  <div class="steps-section">{steps_html}</div>
</div>
{_footer(generated_at)}
</body>
</html>"""


# ── S3 Publishing ─────────────────────────────────────────────────────────────

def _upload_file(s3_client, bucket, key, data, content_type):
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def publish_to_s3(files, s3_client, bucket, region, prefix=MANUAL_PREFIX):
    """Upload {relative_path: (bytes, content_type)} to S3 under prefix/."""
    for path, (data, ct) in files.items():
        _upload_file(s3_client, bucket, f'{prefix}/{path}', data, ct)
    return f'https://{bucket}.s3.{region}.amazonaws.com/{prefix}/index.html'


def get_last_published(s3_client, bucket, region, prefix=MANUAL_PREFIX):
    """Return ISO timestamp of last publish, or None if never published."""
    try:
        obj = s3_client.head_object(Bucket=bucket, Key=f'{prefix}/index.html')
        return obj['LastModified'].strftime('%Y-%m-%d %H:%M UTC')
    except ClientError:
        return None


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_and_publish(teams, all_checklists, checklist_items_fn,
                         image_url_fn, s3_client, bucket, region):
    """
    Generate the full manual and publish to S3.

    Args:
        teams:               list of Team model instances
        all_checklists:      list of active Checklist instances
        checklist_items_fn:  callable(checklist_id) -> list[ChecklistItem]
        image_url_fn:        callable(filename) -> str  (absolute URL)
        s3_client:           boto3 S3 client
        bucket:              S3 bucket name
        region:              AWS region string

    Returns dict: {'url', 'files_uploaded', 'generated_at'}
    """
    generated_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    files = {}  # {relative_path: (bytes, content_type)}

    # Build team → role → checklists mapping
    teams_data = []
    for team in teams:
        team_checklists = [c for c in all_checklists if c.team_id == team.id]
        roles_map = {}
        for c in team_checklists:
            role = c.assigned_role or 'General'
            roles_map.setdefault(role, []).append(c)
        teams_data.append({'team': team, 'checklists': team_checklists, 'roles': roles_map})

    unassigned = [c for c in all_checklists if c.team_id is None]

    # Shared stylesheet
    files['styles.css'] = (SHARED_CSS.encode('utf-8'), 'text/css')

    # Org chart diagram
    org_svg = generate_org_chart_svg(teams_data, unassigned)
    files['diagrams/org-chart.svg'] = (org_svg.encode('utf-8'), 'image/svg+xml')

    # Per-checklist pages + flowchart diagrams
    for checklist in all_checklists:
        items = checklist_items_fn(checklist.id)
        flowchart_svg = generate_flowchart_svg(checklist.name, items)
        files[f'diagrams/flowchart-{checklist.id}.svg'] = (
            flowchart_svg.encode('utf-8'), 'image/svg+xml'
        )
        page_html = generate_checklist_page(
            checklist, items, image_url_fn, flowchart_svg, generated_at
        )
        files[f'checklist-{checklist.id}.html'] = (
            page_html.encode('utf-8'), 'text/html; charset=utf-8'
        )

    # Index / TOC page
    index_html = generate_index_page(teams_data, unassigned, org_svg, generated_at)
    files['index.html'] = (index_html.encode('utf-8'), 'text/html; charset=utf-8')

    url = publish_to_s3(files, s3_client, bucket, region)
    return {'url': url, 'files_uploaded': len(files), 'generated_at': generated_at}
