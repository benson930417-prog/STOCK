#!/usr/bin/env python3
"""Publish the standalone public bedroom viewer; reload only Nginx.

Run as root from the checked-out STOCK release. No authentication is added.
"""
from pathlib import Path
import datetime,hashlib,os,shutil,subprocess

def deploy():
    if os.geteuid()!=0:raise SystemExit('Run with sudo python3 scripts/deploy_bedroom_site.py')
    repo=Path(__file__).resolve().parents[1]
    source=repo/'sites/bedroom-a'
    # The designer-facing site contains one page and its runtime assets only.
    # Evidence pages, sources and editable downloads stay in the project archive.
    public_names=['review.html','viewer.js','outlet-viewer.js','outlets.json',
                  'bedroom-a-v1.glb','architectural-edges.json',
                  '03-bed-to-balcony.png','site-photo.png']
    files=sorted([source/name for name in public_names]+[p for p in (source/'vendor').rglob('*') if p.is_file()])
    missing=[str(p) for p in files if not p.is_file()]
    if missing:raise SystemExit('Missing public assets: '+str(missing))
    if not (source/'review.html').is_file() or not (source/'bedroom-a-v1.glb').is_file():raise SystemExit('Missing site payload')
    digest=hashlib.sha256()
    for p in files:digest.update(p.relative_to(source).as_posix().encode());digest.update(p.read_bytes())
    release_id=digest.hexdigest()[:16]
    base=Path('/var/www/bedroom-a');release=base/'releases'/release_id
    if not release.exists():
        for p in files:
            target=release/p.relative_to(source)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,target)
    for p in release.rglob('*'):p.chmod(0o755 if p.is_dir() else 0o644)
    release.chmod(0o755)
    site=Path('/etc/nginx/sites-enabled/line-bot')
    snippet=Path('/etc/nginx/snippets/bedroom-a.conf')
    old_site=site.read_bytes();old_snippet=snippet.read_bytes() if snippet.exists() else None
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=Path('/var/backups/stock-bedroom')/stamp;backup.mkdir(parents=True)
    (backup/'line-bot.conf').write_bytes(old_site)
    if old_snippet is not None:(backup/'bedroom-a.conf').write_bytes(old_snippet)
    config='''# Standalone public static site, deliberately without login.
location = /bedroom-a { return 301 /bedroom-a/; }
location ^~ /bedroom-a/ {
    alias /var/www/bedroom-a/current/;
    index review.html;
    charset utf-8;
    add_header Cache-Control "no-cache";
}
'''
    text=old_site.decode();include='    include /etc/nginx/snippets/bedroom-a.conf;'
    anchor='    server_name linechatbot.duckdns.org;'
    if include not in text:
        if anchor not in text:raise SystemExit('Expected server block not found; no config changed')
        text=text.replace(anchor,anchor+'\n'+include,1)
    current=base/'current';previous=os.readlink(current) if current.is_symlink() else None
    if current.exists() and not current.is_symlink():raise SystemExit('Expected current to be a symlink')
    pending=base/'current.next'
    try:
        snippet.write_text(config);site.write_text(text)
        subprocess.run(['nginx','-t'],check=True)
        if pending.is_symlink():pending.unlink()
        pending.symlink_to(release,target_is_directory=True);os.replace(pending,current)
        subprocess.run(['systemctl','reload','nginx'],check=True)
    except BaseException:
        site.write_bytes(old_site)
        if old_snippet is None:snippet.unlink(missing_ok=True)
        else:snippet.write_bytes(old_snippet)
        if previous:
            if pending.is_symlink():pending.unlink()
            pending.symlink_to(previous,target_is_directory=True);os.replace(pending,current)
        elif current.is_symlink():current.unlink()
        raise
    print('Published https://linechatbot.duckdns.org/bedroom-a/')
    print('Release:',release_id,'files:',len(files),'backup:',backup)

if __name__=='__main__':deploy()
